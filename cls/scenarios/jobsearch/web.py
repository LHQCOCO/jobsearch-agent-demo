"""求职决策演示页：把一次真实 AgentRuntime 运行渲染成可截图 / 录屏的页面。

只做展示，不改场景与内核：
- 复用 Flask 应用与 app.css 的版式；
- 页面结论全部来自 run.artifacts 与 trace，不做任何示例数据兜底；
- 为了展示「打回 → 第二轮怎么改」，在场景外层包一个角色记录器，
  记录每一轮 tailor 草稿与 auditor 判定（ReviewLoop 本身只保留最终稿）。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import replace
from pathlib import Path

from flask import Blueprint, abort, render_template, request

from ... import config
from ...agent.runtime import AgentRuntime, config_budget
from .manifest import build_scenario
from .roles import JobRoleRunner

jobsearch_bp = Blueprint("jobsearch", __name__)

MAX_JD_CHARS = 40_000
MAX_RESUME_CHARS = 80_000
SAMPLE_JD = "jobs/jd_ai_agent.md"
SAMPLE_RESUME = "jobs/resume_sample.md"

STATUS_LABEL = {"covered": "覆盖", "partial": "部分", "missing": "缺少"}
SEVERITY_LABEL = {"high": "高", "medium": "中", "low": "低"}
EVENT_LABEL = {
    "run.start": "运行开始", "plan.created": "计划生成", "step.start": "步骤开始",
    "step.end": "步骤结束", "tool.call": "工具调用", "tool.result": "工具返回",
    "role.message": "角色产出", "verify.result": "证据核查", "run.end": "运行结束",
}


class RecordingRoleRunner(JobRoleRunner):
    """继承而不是外包装：ReviewLoop 把 ``self`` 当 runner 回调，只有子类能截到
    tailor / auditor 每一轮的调用。记录内容只用于页面展示。"""

    def __init__(self, registry, adapter=None):
        super().__init__(registry, adapter)
        self.rounds: list[dict] = []

    def run(self, role: str, inputs: dict, ctx) -> dict:
        if role == "tailor":
            out = super().run(role, inputs, ctx)
            self.rounds.append({
                "round": len(self.rounds) + 1,
                "bullets": [dict(b) for b in (out.get("bullets") or [])],
                "verdicts": [],
            })
            return out
        if role == "auditor":
            out = super().run(role, inputs, ctx)
            if self.rounds:
                self.rounds[-1]["verdicts"] = list(out.get("verdicts") or [])
            return out
        return super().run(role, inputs, ctx)


def _scenario_with_recorder():
    base = build_scenario()
    holder: dict = {}

    def factory(registry, adapter=None):
        recorder = RecordingRoleRunner(registry, adapter)
        holder["recorder"] = recorder
        return recorder

    return replace(base, role_runner_factory=factory), holder


def _trace_events(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    events: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        parts = []
        for key in ("step_id", "tool", "role", "verdict", "reason_code", "rule_id",
                    "reason", "count", "status", "stop_reason", "error_code",
                    "fallback", "latency_ms"):
            value = item.get(key)
            if value in (None, "", False):
                continue
            parts.append(f"{key}={value}")
        events.append({
            "seq": item.get("seq", 0),
            "event": item.get("event", ""),
            "label": EVENT_LABEL.get(item.get("event", ""), item.get("event", "")),
            "summary": " · ".join(parts),
        })
    return events


def _visible_events(events: list[dict]) -> list[dict]:
    """工具调用/返回占 trace 的大头，页面折叠掉，只留计划、步骤、角色与核查。"""
    return [e for e in events if e["event"] not in ("tool.call", "tool.result")]


def _requirement_map(matches: list[dict]) -> dict:
    return {m.get("req_id", ""): m.get("requirement", "") for m in matches}


def _req_label(req_id: str, req_map: dict) -> str:
    if req_id in req_map:
        return req_map[req_id]
    if req_id == "skills":
        return "技能行（JD 技术栈与简历技能拼接）"
    return req_id


def _round_views(recorder: RecordingRoleRunner | None, matches: list[dict]) -> list[dict]:
    if recorder is None:
        return []
    req_map = _requirement_map(matches)
    views: list[dict] = []
    for item in recorder.rounds:
        verdicts = {v.get("bullet_idx"): v for v in item.get("verdicts") or []}
        bullets = []
        for idx, bullet in enumerate(item.get("bullets") or []):
            verdict = verdicts.get(idx) or {}
            bullets.append({
                "idx": idx,
                "text": bullet.get("text", ""),
                "source_text": bullet.get("source_text", ""),
                "based_on_spans": bullet.get("based_on_spans") or [],
                "req_id": bullet.get("req_id", ""),
                "requirement": _req_label(bullet.get("req_id", ""), req_map),
                "verdict": verdict.get("verdict", ""),
                "reason_code": verdict.get("reason_code", ""),
                "detail": verdict.get("detail", ""),
            })
        views.append({"round": item["round"], "bullets": bullets,
                      "rejected": [b for b in bullets if b["verdict"] == "reject"]})

    for i, view in enumerate(views):
        nxt = views[i + 1] if i + 1 < len(views) else None
        for bullet in view["rejected"]:
            if nxt is None:
                bullet["next_action"] = "已达打回上限，未进入下一轮"
                continue
            same = [b for b in nxt["bullets"] if b["req_id"] == bullet["req_id"]]
            if not same:
                bullet["next_action"] = "第二轮已删除该条，不再拼入简历之外的内容"
                continue
            bullet["next_text"] = same[0]["text"]
            bullet["next_action"] = ("第二轮已改写" if same[0]["text"] != bullet["text"]
                                     else "第二轮沿用原句")
    return views


def _rejected_items(rounds: list[dict]) -> list[dict]:
    items = []
    for view in rounds:
        for bullet in view["rejected"]:
            items.append({**bullet, "round": view["round"]})
    return items


def _read_sample(name: str) -> str:
    path = config.SAMPLES_DIR / name
    return path.read_text(encoding="utf-8")


def _resume_from_request(form) -> tuple[str, str]:
    upload = request.files.get("resume_file")
    if upload is not None and upload.filename:
        raw = upload.read()
        if not raw:
            raise ValueError("上传的简历文件为空")
        suffix = Path(upload.filename).suffix.lower()
        if suffix in (".md", ".txt", ""):
            text = raw.decode("utf-8", errors="replace").lstrip("\ufeff").strip()
        else:
            from ... import parsing

            try:
                doc = parsing.parse(upload.filename, raw)
            except Exception as exc:  # noqa: BLE001 - 解析失败按用户输入错误处理
                raise ValueError(f"简历解析失败：{exc.__class__.__name__}") from exc
            text = (doc.get("text") or "").strip()
        if not text:
            raise ValueError("未能从上传文件中抽取到文字，请改用粘贴")
        return text, upload.filename
    return (form.get("resume_text") or "").strip(), ""


def run_analysis(jd_text: str, resume_text: str, save: bool = False) -> tuple[dict, dict]:
    """真实跑一次 AgentRuntime，返回 (run 元信息, 页面视图)。"""
    jd_text = (jd_text or "").strip()
    resume_text = (resume_text or "").strip()
    if len(jd_text) < 10:
        raise ValueError("JD 正文过短：请粘贴至少 10 个字的岗位描述")
    if len(resume_text) < 20:
        raise ValueError("简历内容过短：请粘贴或上传完整简历")
    if len(jd_text) > MAX_JD_CHARS:
        raise ValueError(f"JD 超过 {MAX_JD_CHARS} 字上限")
    if len(resume_text) > MAX_RESUME_CHARS:
        raise ValueError(f"简历超过 {MAX_RESUME_CHARS} 字上限")

    scenario, holder = _scenario_with_recorder()
    run_id = uuid.uuid4().hex[:12]
    runtime = AgentRuntime(scenario, data_dir=config.DATA_DIR / "agent_traces")
    run = runtime.run(
        {"jd_text": jd_text, "resume_text": resume_text,
         "save": bool(save), "run_id": run_id},
        budget=config_budget(), run_id=run_id,
    )
    artifacts = run.artifacts or {}
    summary = artifacts.get("summary") or {}
    matches = artifacts.get("s3") or []
    if isinstance(matches, dict):
        matches = matches.get("matches") or []

    for item in matches:
        item["status_label"] = STATUS_LABEL.get(item.get("status"), item.get("status", ""))
    gaps = artifacts.get("s4", {}).get("gaps") or []
    for gap in gaps:
        gap["severity_label"] = SEVERITY_LABEL.get(gap.get("severity"), gap.get("severity", ""))
        gap["status_label"] = STATUS_LABEL.get(gap.get("status"), gap.get("status", ""))

    req_map = _requirement_map(matches)
    bullets = []
    for bullet in summary.get("bullets") or []:
        bullets.append({
            **bullet,
            "requirement": req_map.get(bullet.get("req_id", ""), ""),
            "rewritten": bool(bullet.get("source_text")
                              and bullet.get("text") != bullet.get("source_text")),
        })

    rounds = _round_views(holder.get("recorder"), matches)
    trace_all = _trace_events(run.trace_path)
    view = {
        "run_id": run.run_id,
        "status": run.status,
        "stop_reason": run.stop_reason,
        "metrics": run.metrics,
        "jd": artifacts.get("s1") or {},
        "resume": artifacts.get("s2") or {},
        "matches": matches,
        "counts": (artifacts.get("s4") or {}).get("summary") or {},
        "gaps": gaps,
        "bullets": bullets,
        "rejections": summary.get("rejections") or [],
        "rounds": rounds,
        "rejected_items": _rejected_items(rounds),
        "application_id": summary.get("application_id", ""),
        "brief_path": summary.get("brief_path", ""),
        "brief_name": Path(summary.get("brief_path", "")).name,
        "trace_name": Path(run.trace_path).name,
        "trace": _visible_events(trace_all),
        "trace_total": len(trace_all),
        "audit_ok": summary.get("audit_ok"),
        "audit_rounds": summary.get("audit_rounds", 0),
    }
    return {
        "run_id": run.run_id,
        "status": run.status,
        "stop_reason": run.stop_reason,
        "metrics": run.metrics,
        "trace_name": view["trace_name"],
    }, view


@jobsearch_bp.get("/jobsearch")
def jobsearch_page():
    autorun = request.args.get("autorun") == "1"
    result, run_meta, error = None, None, ""
    jd_text = resume_text = ""
    if autorun:
        try:
            jd_text = _read_sample(SAMPLE_JD)
            resume_text = _read_sample(SAMPLE_RESUME)
            run_meta, result = run_analysis(jd_text, resume_text, save=False)
        except ValueError as exc:
            error = str(exc)
    return render_template(
        "jobsearch.html",
        result=result, run_meta=run_meta, error=error,
        jd_text=jd_text, resume_text=resume_text, autorun=autorun,
    )


@jobsearch_bp.post("/jobsearch/analyze")
def jobsearch_analyze():
    jd_text = (request.form.get("jd_text") or "").strip()
    error = ""
    result = None
    run_meta = None
    resume_text = ""
    try:
        resume_text, _filename = _resume_from_request(request.form)
        save = request.form.get("save") == "on"
        run_meta, result = run_analysis(jd_text, resume_text, save=save)
    except ValueError as exc:
        error = str(exc)
    status = 400 if error else 200
    return render_template(
        "jobsearch.html",
        result=result, run_meta=run_meta, error=error,
        jd_text=jd_text, resume_text=(request.form.get("resume_text") or ""),
        autorun=False,
    ), status


@jobsearch_bp.get("/jobsearch/brief/<run_id>")
def jobsearch_brief(run_id: str):
    if not run_id.isalnum() or len(run_id) > 32:
        abort(404)
    path = config.EXPORTS_DIR / f"{run_id}-brief.md"
    if not path.exists():
        abort(404)
    return render_template("jobsearch_brief.html", content=path.read_text(encoding="utf-8"),
                           run_id=run_id)
