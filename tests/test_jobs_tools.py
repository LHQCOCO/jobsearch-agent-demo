"""求职场景工具 J1–J7：解析、检索、匹配、事实比对、落库与决策卡。"""
from __future__ import annotations

from pathlib import Path

import pytest

from cls import config
from cls.agent.context import RunContext
from cls.agent.evidence import EvidenceChecker
from cls.agent.registry import ToolRegistry
from cls.agent.trace import Trace
from cls.agent.types import INVALID_ARGS, Budget
from cls.scenarios.jobsearch.documents import JDS, RESUMES
from cls.scenarios.jobsearch.tools import TOOL_SPECS

ROOT = Path(__file__).resolve().parents[1]
JD_TEXT = (ROOT / "samples" / "jobs" / "jd_ai_agent.md").read_text(encoding="utf-8")
CV_TEXT = (ROOT / "samples" / "jobs" / "resume_sample.md").read_text(encoding="utf-8")


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
    return RunContext(run_id="test-jobs", trace=Trace(tmp_path / "trace.jsonl", "test-jobs"),
                      budget=Budget(), state={"results": {}})


def _load(registry: ToolRegistry, ctx: RunContext) -> tuple[dict, dict]:
    jd = registry.call("parse_jd", {"raw_text": JD_TEXT}, ctx)
    cv = registry.call("load_resume", {"text": CV_TEXT}, ctx)
    assert jd.ok and cv.ok
    ctx.state["results"] = {"s1": jd.data, "s2": cv.data}
    return jd.data, cv.data


def test_parse_jd_splits_must_nice_and_meta(registry, ctx):
    jd = registry.call("parse_jd", {"raw_text": JD_TEXT}, ctx)
    assert jd.ok
    data = jd.data
    assert data["title"] == "AI 应用研发工程师（Agent 方向）"
    assert data["company"] == "上海某某科技有限公司"
    assert data["years"] == "3年以上"

    must_ids = [r["req_id"] for r in data["must_have"]]
    assert must_ids == [f"req-{i:02d}" for i in range(1, len(must_ids) + 1)]
    assert any("Python" in r["text"] for r in data["must_have"])
    assert any("Docker" in r["text"] for r in data["nice_to_have"])
    assert {"Python", "Ollama", "BM25", "Docker"} <= set(data["tech"])
    assert data["responsibilities"]


def test_parse_jd_rejects_short_text(registry, ctx):
    result = registry.call("parse_jd", {"raw_text": "太短"}, ctx)
    assert not result.ok
    assert result.error_code == INVALID_ARGS


def test_load_resume_sections_entities_and_stats(registry, ctx):
    result = registry.call("load_resume", {"text": CV_TEXT}, ctx)
    assert result.ok
    data = result.data
    names = [s["name"] for s in data["sections"]]
    assert "技能" in names and "项目经历" in names and "教育经历" in names
    tech = {e["value"] for e in data["entities"]["tech"]}
    assert {"Python", "Ollama", "BM25", "Git"} <= tech
    assert data["entities"]["metric"] and data["entities"]["date"]
    assert data["stats"]["chars"] == len(CV_TEXT.strip())
    assert data["stats"]["sentences"] > 5


def test_load_resume_requires_text_or_path(registry, ctx):
    result = registry.call("load_resume", {}, ctx)
    assert not result.ok
    assert result.error_code == INVALID_ARGS


def test_load_resume_reads_local_path(registry, ctx, tmp_path):
    path = tmp_path / "cv.md"
    path.write_text(CV_TEXT, encoding="utf-8")
    result = registry.call("load_resume", {"path": str(path)}, ctx)
    assert result.ok
    assert result.data["resume_id"].startswith("cv-")


def test_search_resume_hits_are_locatable(registry, ctx):
    _load(registry, ctx)
    resume = RESUMES.get(ctx.state["results"]["s2"]["resume_id"])
    result = registry.call("search_resume", {
        "resume_id": resume.resume_id, "query": "Ollama 本地推理", "top_k": 3}, ctx)
    assert result.ok and result.data["hits"]
    for hit in result.data["hits"]:
        check = EvidenceChecker.check_span(resume.text, hit["quote"], hit["span"]["start"])
        assert check.ok, check.detail


def test_match_requirements_status_and_evidence(registry, ctx):
    _load(registry, ctx)
    jd_id = ctx.state["results"]["s1"]["jd_id"]
    resume_id = ctx.state["results"]["s2"]["resume_id"]
    resume = RESUMES.get(resume_id)
    result = registry.call("match_requirements",
                           {"jd_id": jd_id, "resume_id": resume_id}, ctx)
    assert result.ok
    by_req = {item["req_id"]: item for item in result.data}
    assert len(by_req) == 9
    assert by_req["req-03"]["status"] == "covered"       # Ollama
    assert by_req["req-07"]["status"] == "missing"       # Docker / Kubernetes
    for item in result.data:
        if item["status"] != "missing":
            assert item["evidence"], item["req_id"]
        for evidence in item["evidence"]:
            check = EvidenceChecker.check_span(resume.text, evidence["quote"],
                                               evidence["span"]["start"])
            assert check.ok, check.detail


def test_match_requirement_marks_unverifiable_years(registry, ctx):
    _load(registry, ctx)
    result = registry.call("match_requirements", {
        "jd_id": ctx.state["results"]["s1"]["jd_id"],
        "resume_id": ctx.state["results"]["s2"]["resume_id"],
        "req_ids": ["req-02"]}, ctx)
    assert result.ok
    item = result.data[0]
    assert item["status"] == "partial"
    assert "年限" in item["reason"]
    assert item["confidence"] <= 0.5


def test_detect_new_facts_blocks_fabricated_entities(registry, ctx):
    _load(registry, ctx)
    resume_id = ctx.state["results"]["s2"]["resume_id"]
    draft = "主导 Kubernetes 集群迁移，2025 年 11 月上线，服务 300 家客户"
    result = registry.call("detect_new_facts",
                           {"draft_text": draft, "resume_id": resume_id}, ctx)
    assert result.ok
    kinds = {item["type"] for item in result.data["added"]}
    values = {item["entity"] for item in result.data["added"]}
    assert "Kubernetes" in values
    assert {"tech", "date"} <= kinds
    assert result.data["ok"] is False


def test_detect_new_facts_allows_wording_change(registry, ctx):
    _load(registry, ctx)
    resume = RESUMES.get(ctx.state["results"]["s2"]["resume_id"])
    source = next(s["text"] for s in resume.sentences if "Ollama" in s["text"])
    result = registry.call("detect_new_facts", {
        "draft_text": source.replace("接入", "基于"), "resume_id": resume.resume_id}, ctx)
    assert result.ok
    assert result.data["added"] == []
    assert result.data["ok"] is True


def test_save_application_upserts_same_jd(registry, ctx, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "history.db")
    _load(registry, ctx)
    jd_id = ctx.state["results"]["s1"]["jd_id"]
    first = registry.call("save_application",
                          {"action": "save", "jd_id": jd_id, "status": "analyzed"}, ctx)
    second = registry.call("save_application",
                           {"action": "save", "jd_id": jd_id, "status": "applied"}, ctx)
    assert first.ok and second.ok
    assert first.data["application_id"] == second.data["application_id"]
    listing = registry.call("save_application", {"action": "list"}, ctx)
    assert listing.ok
    assert len(listing.data["items"]) == 1
    assert listing.data["items"][0]["status"] == "applied"
    assert listing.data["items"][0]["title"] == "AI 应用研发工程师（Agent 方向）"


def test_render_brief_writes_markdown(registry, ctx, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path / "exports")
    _load(registry, ctx)
    ctx.state["results"].update({
        "s3": registry.call("match_requirements", {
            "jd_id": ctx.state["results"]["s1"]["jd_id"],
            "resume_id": ctx.state["results"]["s2"]["resume_id"]}, ctx).data,
        "s4": {"summary": {"total": 1, "covered": 1, "partial": 0, "missing": 0},
               "gaps": []},
        "s5": {"ok": True, "rounds": 1,
               "draft": {"bullets": [{"text": "改写句", "based_on_spans": [1]}]},
               "verdict": {"verdicts": []}},
    })
    result = registry.call("render_brief", {"run_id": "r1"}, ctx)
    assert result.ok
    path = Path(result.data["path"])
    assert path.exists()
    body = path.read_text(encoding="utf-8")
    assert "匹配矩阵" in body and "审核结果" in body and "改写建议" in body
    assert result.data["bytes"] > 100


def test_tool_specs_are_strict_and_registered():
    names = {spec.name for spec, _ in TOOL_SPECS}
    assert names == {"parse_jd", "load_resume", "search_resume", "match_requirements",
                     "detect_new_facts", "save_application", "render_brief"}
    for spec, fn in TOOL_SPECS:
        assert callable(fn)
        assert spec.params.get("additionalProperties") is False
        assert spec.side_effect in ("none", "local_write")
        assert spec.timeout_s > 0
