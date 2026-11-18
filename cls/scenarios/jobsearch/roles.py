"""求职场景的三个角色：analyst 汇总、tailor 改写、auditor 审核。

连同 review 编排一起放在场景层：ReviewLoop 由内核提供，这里只决定
「谁产出、谁核查、打回后怎么改」。所有结论都来自工具返回，不编造 span。
"""
from __future__ import annotations

import time

from ...agent.evidence import EvidenceChecker
from ...agent.roles import Role, ReviewLoop, call_tool_for_role
from ...agent.types import ROLE_ERROR
from . import extract
from .documents import JDS, RESUMES
from .prompts import AUDITOR_PROMPT, TAILOR_PROMPT

ANALYST_ROLE = Role(
    name="analyst",
    system_prompt=(
        "你是求职分析师。逐条比对 JD 要求与简历证据，只依据工具返回的判定与原文；"
        "每条判定必须带简历 span，不得推测简历里没有的经历。"
    ),
    allowed_tools=("parse_jd", "load_resume", "search_resume", "match_requirements"),
    output_schema={
        "type": "object", "required": ["matches", "summary", "gaps"],
        "properties": {
            "matches": {"type": "array", "items": {"type": "object"}},
            "summary": {"type": "object"},
            "gaps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["req_id", "gap", "severity", "options"],
                    "properties": {
                        "req_id": {"type": "string"},
                        "gap": {"type": "string"},
                        "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                        "options": {"type": "array", "items": {"type": "string"}},
                    },
                    "additionalProperties": True,
                },
            },
        },
        "additionalProperties": True,
    },
    max_tokens=700,
)

TAILOR_ROLE = Role(
    name="tailor",
    system_prompt=TAILOR_PROMPT,
    allowed_tools=("search_resume",),
    output_schema={
        "type": "object", "required": ["bullets"],
        "properties": {
            "bullets": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["text", "based_on_spans"],
                    "properties": {
                        "text": {"type": "string"},
                        "based_on_spans": {"type": "array", "items": {"type": "integer"}},
                    },
                    "additionalProperties": True,
                },
            },
        },
        "additionalProperties": True,
    },
    max_tokens=700,
)

AUDITOR_ROLE = Role(
    name="auditor",
    system_prompt=AUDITOR_PROMPT,
    allowed_tools=("detect_new_facts", "search_resume"),
    output_schema={
        "type": "object", "required": ["verdicts"],
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["bullet_idx", "verdict", "reason_code"],
                    "properties": {
                        "bullet_idx": {"type": "integer"},
                        "verdict": {"type": "string", "enum": ["confirmed", "reject"]},
                        "reason_code": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
            },
        },
        "additionalProperties": True,
    },
    max_tokens=700,
)

# 只替换措辞同义词，不引入任何新实体
_REPLACE = (
    ("负责", "承担"), ("使用", "基于"), ("熟悉", "掌握"),
    ("接入", "基于"), ("构建", "搭建"), ("编写", "撰写"), ("完成", "落地"),
)


def _rephrase(text: str) -> str:
    out = text
    for src, dst in _REPLACE:
        if src != dst:
            out = out.replace(src, dst)
    return out


def _sentence_index(resume, span: dict | None) -> int | None:
    if not span:
        return None
    for i, sent in enumerate(resume.sentences):
        if sent["span"]["start"] == span.get("start"):
            return i
    return None


class JobRoleRunner:
    """确定性角色执行；模型只作为规划器的可选增强，不参与判定。"""

    def __init__(self, registry, adapter=None):
        self.registry = registry
        self.adapter = adapter
        self.review = ReviewLoop(producer="tailor", verifier="auditor", max_rejections=2)

    def run(self, role: str, inputs: dict, ctx) -> dict:
        if role == "review":
            return self.review.run(self, ctx, inputs)
        handler = {"analyst": self._analyze, "tailor": self._tailor,
                   "auditor": self._audit}.get(role)
        if handler is None:
            return {"_error": ROLE_ERROR}
        started = time.perf_counter()
        data = handler(inputs, ctx)
        ctx.emit("role.message", role=role, ok=not data.get("_error"),
                 latency_ms=int((time.perf_counter() - started) * 1000),
                 tokens_in=0, tokens_out=0, fallback=True,
                 count=len(data.get("matches") or data.get("bullets")
                           or data.get("verdicts") or []))
        return data

    # ---------------------------------------------------------------- analyst
    def _analyze(self, inputs: dict, ctx) -> dict:
        results = ctx.state.get("results", {})
        jd = results.get("s1") or {}
        matches = results.get("s3") or []
        if isinstance(matches, dict):
            matches = matches.get("matches", [])
        jd_record = JDS.get(jd.get("jd_id", ""))
        requirements = {}
        must_ids = set()
        if jd_record is not None:
            for item in jd_record.parsed.get("must_have", []):
                requirements[item["req_id"]] = item["text"]
                must_ids.add(item["req_id"])
            for item in jd_record.parsed.get("nice_to_have", []):
                requirements[item["req_id"]] = item["text"]

        summary = {
            "total": len(matches),
            "covered": sum(1 for m in matches if m.get("status") == "covered"),
            "partial": sum(1 for m in matches if m.get("status") == "partial"),
            "missing": sum(1 for m in matches if m.get("status") == "missing"),
        }

        gaps: list[dict] = []
        for item in matches:
            status = item.get("status")
            if status == "covered":
                continue
            req_id = item.get("req_id", "")
            severity = "high" if req_id in must_ids else "medium"
            options = (["补作品", "换表述", "放弃"] if status == "missing"
                       else ["换表述", "补作品"])
            nearest = call_tool_for_role(
                ANALYST_ROLE, self.registry, "search_resume",
                {"resume_id": (results.get("s2") or {}).get("resume_id", ""),
                 "query": item.get("requirement", ""), "top_k": 1}, ctx)
            hits = nearest.data.get("hits", []) if nearest.ok else []
            gaps.append({
                "req_id": req_id,
                "gap": item.get("requirement") or requirements.get(req_id, ""),
                "severity": severity,
                "status": status,
                "options": options,
                "reason": item.get("reason", ""),
                "nearest_hit": (hits[0]["quote"] if hits else ""),
            })
        gaps.sort(key=lambda g: (g["status"] != "missing", g["severity"] != "high"))

        return {"matches": matches, "summary": summary, "gaps": gaps[:8], "fallback": True}

    # ---------------------------------------------------------------- tailor
    def _tailor(self, inputs: dict, ctx) -> dict:
        results = ctx.state.get("results", {})
        jd = results.get("s1") or {}
        resume = RESUMES.get((results.get("s2") or {}).get("resume_id", ""))
        matches = results.get("s3") or []
        if isinstance(matches, dict):
            matches = matches.get("matches", [])
        retry = bool(inputs.get("reasons"))

        bullets: list[dict] = []
        used: set[int] = set()
        if resume is not None:
            ordered = sorted(matches, key=lambda m: {"covered": 0, "partial": 1,
                                                     "missing": 2}.get(m.get("status"), 3))
            for item in ordered:
                if len(bullets) >= 3:
                    break
                hit = call_tool_for_role(
                    TAILOR_ROLE, self.registry, "search_resume",
                    {"resume_id": resume.resume_id,
                     "query": item.get("requirement", ""), "top_k": 3}, ctx)
                choice: tuple[int, dict] | None = None
                for candidate in (hit.data.get("hits", []) if hit.ok else []):
                    idx = _sentence_index(resume, candidate.get("span"))
                    if idx is None or idx in used:
                        continue
                    choice = (idx, candidate)
                    # 优先项目/工作经历里的句子，技能行只作兜底
                    if any(k in candidate.get("section", "") for k in ("项目", "工作", "实习")):
                        break
                if choice is None:
                    for evidence in item.get("evidence") or []:
                        idx = _sentence_index(resume, evidence.get("span"))
                        if idx is not None and idx not in used:
                            choice = (idx, evidence)
                            break
                if choice is None:
                    continue
                idx, _ = choice
                used.add(idx)
                source = resume.sentences[idx]["text"]
                bullets.append({
                    "text": _rephrase(source),
                    "source_text": source,
                    "based_on_spans": [idx],
                    "req_id": item.get("req_id", ""),
                })

        # 第一轮按 JD 关键词补齐（常见做法）；被审核打回后只保留简历既有事实
        if not retry and resume is not None:
            resume_tech = [e["value"] for e in resume.entities.get("tech", [])]
            missing = [t for t in (jd.get("tech") or []) if t not in resume_tech]
            if missing:
                bullets.append({
                    "text": "技能：" + "、".join(dict.fromkeys(resume_tech + missing)),
                    "based_on_spans": sorted(used)[:3],
                    "req_id": "skills",
                })

        return {"bullets": bullets, "fallback": True}

    # ---------------------------------------------------------------- auditor
    def _audit(self, inputs: dict, ctx) -> dict:
        draft = inputs.get("draft") or {}
        results = ctx.state.get("results", {})
        resume = RESUMES.get((results.get("s2") or {}).get("resume_id", ""))
        verdicts: list[dict] = []

        for i, bullet in enumerate(draft.get("bullets") or []):
            text = (bullet.get("text") or "").strip()
            spans = bullet.get("based_on_spans")
            base = {"bullet_idx": i, "rule_id": bullet.get("req_id", "")}
            if not text or not isinstance(spans, list) or not spans:
                verdicts.append({**base, "verdict": "reject",
                                 "reason_code": "SCHEMA_INVALID",
                                 "detail": "缺少 text 或 based_on_spans"})
                continue
            if resume is None:
                verdicts.append({**base, "verdict": "reject",
                                 "reason_code": "EVIDENCE_MISSING",
                                 "detail": "简历未加载"})
                continue

            bad = ""
            for idx in spans:
                if not isinstance(idx, int) or not (0 <= idx < len(resume.sentences)):
                    bad = f"句子序号越界：{idx}"
                    break
                sent = resume.sentences[idx]
                check = EvidenceChecker.check_span(resume.text, sent["text"],
                                                   sent["span"]["start"])
                if not check.ok:
                    bad = check.detail
                    break
            if bad:
                verdicts.append({**base, "verdict": "reject",
                                 "reason_code": "EVIDENCE_MISSING", "detail": bad})
                continue

            found = call_tool_for_role(
                AUDITOR_ROLE, self.registry, "detect_new_facts",
                {"draft_text": text, "resume_id": resume.resume_id}, ctx)
            added = found.data.get("added", []) if found.ok else []
            if added:
                kinds = ",".join(sorted({a["type"] for a in added}))
                values = "、".join(a["entity"] for a in added[:3])
                verdicts.append({**base, "verdict": "reject",
                                 "reason_code": "FACT_ADDED",
                                 "detail": f"新增{values}（{kinds}）"})
                continue

            verdicts.append({**base, "verdict": "confirmed", "reason_code": ""})

        confirmed = sum(1 for v in verdicts if v["verdict"] == "confirmed")
        rejected = sum(1 for v in verdicts if v["verdict"] == "reject")
        round_no = int(ctx.state.get("jobsearch_audit_round", 0)) + 1
        ctx.state["jobsearch_audit_round"] = round_no
        history = ctx.state.setdefault("jobsearch_rejections", [])
        for item in verdicts:
            if item["verdict"] == "reject":
                history.append({"round": round_no, "reason_code": item["reason_code"],
                                "detail": item.get("detail", ""),
                                "rule_id": item.get("rule_id", "")})
        for v in verdicts:
            ctx.emit("verify.result", role="auditor", ok=v["verdict"] == "confirmed",
                     rule_id=v.get("rule_id", ""), verdict=v["verdict"],
                     reason_code=v.get("reason_code", ""))
        return {"verdicts": verdicts, "confirmed": confirmed,
                "rejected": rejected, "fallback": True}
