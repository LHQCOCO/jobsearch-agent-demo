"""求职场景的证据层与规划器：打回、回退、端到端可跑通。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cls import config
from cls.agent.adapter import ModelReply, ModelUnavailable
from cls.agent.context import RunContext
from cls.agent.registry import ToolRegistry
from cls.agent.trace import Trace
from cls.agent.types import Budget
from cls.scenarios import SCENARIOS
from cls.scenarios.jobsearch.documents import JDS, RESUMES
from cls.scenarios.jobsearch.planner import JobPlanner, build_steps
from cls.scenarios.jobsearch.roles import JobRoleRunner
from cls.scenarios.jobsearch.tools import TOOL_SPECS

ROOT = Path(__file__).resolve().parents[1]
JD_TEXT = (ROOT / "samples" / "jobs" / "jd_ai_agent.md").read_text(encoding="utf-8")
CV_TEXT = (ROOT / "samples" / "jobs" / "resume_sample.md").read_text(encoding="utf-8")


class _FakeAdapter:
    def __init__(self, text: str = "", error: Exception | None = None):
        self.text = text
        self.error = error
        self.calls = 0

    def complete(self, messages, schema=None, max_tokens=800, temperature=0.0):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return ModelReply(text=self.text)


@pytest.fixture(autouse=True)
def _clean_stores():
    JDS.clear()
    RESUMES.clear()
    yield
    JDS.clear()
    RESUMES.clear()


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    for spec, fn in TOOL_SPECS:
        reg.register(spec, fn)
    return reg


@pytest.fixture
def ctx(tmp_path) -> RunContext:
    return RunContext(run_id="test-audit", trace=Trace(tmp_path / "trace.jsonl", "test-audit"),
                      budget=Budget(), state={"results": {}})


def _prepare(registry: ToolRegistry, ctx: RunContext):
    jd = registry.call("parse_jd", {"raw_text": JD_TEXT}, ctx)
    cv = registry.call("load_resume", {"text": CV_TEXT}, ctx)
    assert jd.ok and cv.ok
    ctx.state["results"] = {"s1": jd.data, "s2": cv.data}
    return jd.data, cv.data


# ---------------------------------------------------------------- auditor
def test_auditor_rejects_bullet_without_spans(registry, ctx):
    _, cv = _prepare(registry, ctx)
    runner = JobRoleRunner(registry)
    result = runner.run("auditor", {"draft": {"bullets": [
        {"text": "负责大规模系统重构", "req_id": "req-01"}]}}, ctx)
    assert result["verdicts"][0]["verdict"] == "reject"
    assert result["verdicts"][0]["reason_code"] == "SCHEMA_INVALID"
    assert result["rejected"] == 1


def test_auditor_rejects_out_of_range_span(registry, ctx):
    _prepare(registry, ctx)
    runner = JobRoleRunner(registry)
    result = runner.run("auditor", {"draft": {"bullets": [
        {"text": "x", "based_on_spans": [999], "req_id": "req-02"}]}}, ctx)
    assert result["verdicts"][0]["reason_code"] == "EVIDENCE_MISSING"


def test_auditor_rejects_fabricated_tech(registry, ctx):
    _prepare(registry, ctx)
    runner = JobRoleRunner(registry)
    result = runner.run("auditor", {"draft": {"bullets": [
        {"text": "主导 Kubernetes 与 LangChain 落地", "based_on_spans": [1],
         "req_id": "req-07"}]}}, ctx)
    verdict = result["verdicts"][0]
    assert verdict["verdict"] == "reject"
    assert verdict["reason_code"] == "FACT_ADDED"
    assert "Kubernetes" in verdict["detail"]


def test_auditor_confirms_evidence_bound_bullet(registry, ctx):
    _prepare(registry, ctx)
    resume = RESUMES.get(ctx.state["results"]["s2"]["resume_id"])
    idx = next(i for i, s in enumerate(resume.sentences) if "Ollama" in s["text"])
    runner = JobRoleRunner(registry)
    result = runner.run("auditor", {"draft": {"bullets": [
        {"text": resume.sentences[idx]["text"], "based_on_spans": [idx],
         "req_id": "req-03"}]}}, ctx)
    assert result["verdicts"][0]["verdict"] == "confirmed"
    assert result["rejected"] == 0


# ---------------------------------------------------------------- planner
def test_planner_template_has_no_extra_steps():
    plan = JobPlanner().plan({"jd_text": "x", "resume_text": "y"}, [], Budget())
    assert plan.reason == "template"
    assert [s.id for s in plan.steps] == ["s1", "s2", "s3", "s4", "s5", "s7"]


def test_planner_falls_back_when_model_unavailable():
    adapter = _FakeAdapter(error=ModelUnavailable("offline"))
    plan = JobPlanner(adapter=adapter).plan({"jd_text": "x", "resume_text": "y"}, [], Budget())
    assert plan.reason == "fallback:MODEL_UNAVAILABLE"
    assert len(plan.steps) == 6
    assert adapter.calls == 1


def test_planner_falls_back_on_invalid_json():
    adapter = _FakeAdapter(text="这不是 JSON")
    plan = JobPlanner(adapter=adapter).plan({"jd_text": "x", "resume_text": "y"}, [], Budget())
    assert plan.reason == "fallback:PLAN_INVALID"
    assert adapter.calls == 2


def test_planner_accepts_valid_subset():
    adapter = _FakeAdapter(text=json.dumps({"steps": ["s1", "s2", "s3", "s4"]}))
    plan = JobPlanner(adapter=adapter).plan({"jd_text": "x", "resume_text": "y"}, [], Budget())
    assert plan.reason == "llm"
    assert [s.id for s in plan.steps] == ["s1", "s2", "s3", "s4"]


def test_plan_steps_use_registered_tools_and_roles():
    steps = build_steps({"jd_text": "x", "resume_text": "y", "save": True, "run_id": "r"})
    tool_names = {spec.name for spec, _ in TOOL_SPECS}
    role_names = {"analyst", "tailor", "auditor", "review"}
    ids = [s.id for s in steps]
    for step in steps:
        if step.tool:
            assert step.tool in tool_names
        if step.role:
            assert step.role in role_names
        for dep in step.depends_on:
            assert dep in ids
            assert ids.index(dep) < ids.index(step.id)


# ---------------------------------------------------------------- 场景注册
def test_jobsearch_scenario_is_registered():
    from cls.scenarios.jobsearch.manifest import JOBSEARCH_SCENARIO

    assert SCENARIOS["jobsearch"] is JOBSEARCH_SCENARIO
    assert len(JOBSEARCH_SCENARIO.tools) == 7
    assert set(JOBSEARCH_SCENARIO.roles) == {"analyst", "tailor", "auditor"}


# ---------------------------------------------------------------- 端到端
@pytest.fixture
def run(tmp_path, monkeypatch):
    from cls.agent.runtime import AgentRuntime
    from cls.scenarios.jobsearch.manifest import JOBSEARCH_SCENARIO

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path / "data" / "exports")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "data" / "history.db")
    runtime = AgentRuntime(JOBSEARCH_SCENARIO, data_dir=tmp_path / "traces")
    return runtime.run(
        {"jd_text": JD_TEXT, "resume_text": CV_TEXT, "save": True, "run_id": "e2e"},
        budget=Budget(), run_id="e2e", trace_path=tmp_path / "trace.jsonl",
    )


def test_e2e_runs_and_produces_brief(run):
    assert run.status == "ok"
    assert run.metrics["failures"] == 0
    summary = run.artifacts["summary"]
    counts = summary["match_counts"]
    assert counts["total"] == 9
    assert sum(counts[k] for k in ("covered", "partial", "missing")) == 9
    assert summary["audit_ok"] is True
    assert summary["audit_rounds"] == 2
    assert Path(summary["brief_path"]).exists()
    assert summary["application_id"]


def test_e2e_rejection_is_recorded_and_traceable(run):
    summary = run.artifacts["summary"]
    assert summary["rejections"], "首轮关键词回填应被审核打回"
    first = summary["rejections"][0]
    assert first["reason_code"] == "FACT_ADDED"
    assert first["round"] == 1

    events = [json.loads(line) for line in Path(run.trace_path).read_text().splitlines() if line]
    assert any(e.get("reason_code") == "FACT_ADDED" for e in events)
    assert {"run.start", "plan.created", "step.start", "tool.call", "tool.result",
            "role.message", "verify.result", "step.end", "run.end"} <= {e["event"] for e in events}


def test_e2e_bullets_are_grounded_in_resume(run):
    bullets = run.artifacts["summary"]["bullets"]
    assert bullets
    for bullet in bullets:
        assert bullet["source_text"] in CV_TEXT
        assert bullet["text"] == bullet["text"].strip()


def test_e2e_trace_does_not_leak_resume_or_jd(run):
    raw = Path(run.trace_path).read_text(encoding="utf-8")
    for secret in ("Ollama", "63,653", "金融学硕士", "上海某某科技有限公司", str(ROOT)):
        assert secret not in raw
