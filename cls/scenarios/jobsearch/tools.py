"""求职场景工具 J1–J7。

所有工具都只做本地计算：JD 由用户粘贴、简历由用户指定，不联网、不投递。
每条证据都在返回前用 EvidenceChecker 比对一次，保证「有引用 ⇒ 能定位」。
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from ... import config
from ...agent.evidence import EvidenceChecker
from ...agent.types import INVALID_ARGS, SOURCE_NOT_FOUND, ToolResult, ToolSpec
from . import applications, extract
from .documents import JDS, RESUMES

_YEARS_IN_REQ = re.compile(r"\d{1,2}\s*年(?:及)?以上|\d{1,2}\s*[-~－—至]\s*\d{1,2}\s*年")
_RESUME_YEARS = re.compile(r"\d{1,2}\s*年(?:及)?以上.{0,8}(?:经验|工作)|\d{1,2}\s*年.{0,8}工作经验")


# ------------------------------------------------------------------ helpers
def _read_resume_file(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(path)
    suffix = p.suffix.lower()
    if suffix in (".md", ".txt", ""):
        return p.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")
    # 复用现有解析器处理 docx / pdf，不复制解析逻辑
    from ... import parsing

    doc = parsing.parse(p.name, p.read_bytes())
    return doc.get("text", "") or ""


def _contains(text_lower: str, term: str) -> bool:
    term = (term or "").lower()
    if not term:
        return False
    if term.isascii():
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text_lower) is not None
    return term in text_lower


def _search(resume, query: str, top_k: int) -> list[dict]:
    query = (query or "").strip()
    if not query:
        return []
    scored: list[tuple[float, dict]] = []
    for sent in resume.sentences:
        value = extract.score(query, sent["text"])
        if value > 0:
            scored.append((value, sent))
    scored.sort(key=lambda x: -x[0])

    hits: list[dict] = []
    seen: set[int] = set()
    for value, sent in scored:
        start = sent["span"]["start"]
        if start in seen:
            continue
        seen.add(start)
        hits.append({"section": sent.get("section", ""), "span": dict(sent["span"]),
                     "quote": sent["text"], "score": round(value, 4)})
        if len(hits) >= max(1, int(top_k)):
            break
    return hits


def _verified_evidence(resume, hits: list[dict], limit: int = 2) -> list[dict]:
    out: list[dict] = []
    for hit in hits:
        check = EvidenceChecker.check_span(resume.text, hit["quote"], hit["span"]["start"])
        if not check.ok:
            continue
        out.append({"span": dict(hit["span"]), "quote": hit["quote"],
                    "section": hit.get("section", "")})
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ J1
def parse_jd(args: dict, ctx) -> ToolResult:
    raw = (args.get("raw_text") or "").strip()
    if len(raw) < 10:
        return ToolResult(False, error_code=INVALID_ARGS,
                          data={"details": [{"path": "$.raw_text",
                                             "message": "JD 正文过短，无法解析"}]},
                          message="JD 正文过短")
    source_url = (args.get("source_url") or "").strip()
    jd_id = JDS.add(raw, source_url)
    parsed = extract.parse_jd(raw, source_url)
    record = JDS.get(jd_id)
    if record is not None:
        record.parsed = parsed
    return ToolResult(True, data={
        "jd_id": jd_id,
        "title": parsed["title"],
        "company": parsed["company"],
        "years": parsed["years"],
        "must_have": parsed["must_have"],
        "nice_to_have": parsed["nice_to_have"],
        "tech": parsed["tech"],
        "responsibilities": parsed["responsibilities"],
        "source_url": source_url,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    })


# ------------------------------------------------------------------ J2
def load_resume(args: dict, ctx) -> ToolResult:
    text = (args.get("text") or "").strip()
    path = (args.get("path") or "").strip()
    filename = ""
    if not text:
        if not path:
            return ToolResult(False, error_code=INVALID_ARGS,
                              data={"details": [{"path": "$",
                                                 "message": "text 与 path 至少给一个"}]},
                              message="缺少简历内容")
        try:
            text = _read_resume_file(path).strip()
        except Exception as exc:  # noqa: BLE001 - 读不到文件按来源不存在处理
            return ToolResult(False, error_code=SOURCE_NOT_FOUND,
                              message=f"简历不可读：{exc.__class__.__name__}")
        filename = Path(path).name
    if len(text) < 20:
        return ToolResult(False, error_code=INVALID_ARGS, message="简历内容过短")

    resume_id = RESUMES.add(text, filename)
    sections = extract.split_sections(text)
    sentences = extract.resume_sentences(sections)
    entities = extract.extract_entities(text)
    record = RESUMES.get(resume_id)
    if record is not None:
        record.sections = sections
        record.sentences = sentences
        record.entities = entities

    return ToolResult(True, data={
        "resume_id": resume_id,
        "sections": [{"name": s["name"], "span": s["span"], "text": s["text"]}
                     for s in sections],
        "entities": entities,
        "stats": {"chars": len(text), "sections": len(sections),
                  "sentences": len(sentences)},
    })


# ------------------------------------------------------------------ J3
def search_resume(args: dict, ctx) -> ToolResult:
    resume = RESUMES.get(args.get("resume_id", ""))
    if resume is None:
        return ToolResult(False, error_code=SOURCE_NOT_FOUND, message="简历未加载")
    query = (args.get("query") or "").strip()
    return ToolResult(True, data={"hits": _search(resume, query, int(args.get("top_k", 5)))})


# ------------------------------------------------------------------ J4
def match_requirements(args: dict, ctx) -> ToolResult:
    jd = JDS.get(args.get("jd_id", ""))
    resume = RESUMES.get(args.get("resume_id", ""))
    if jd is None:
        return ToolResult(False, error_code=SOURCE_NOT_FOUND, message="JD 未解析")
    if resume is None:
        return ToolResult(False, error_code=SOURCE_NOT_FOUND, message="简历未加载")

    requirements = jd.parsed.get("must_have", []) + jd.parsed.get("nice_to_have", [])
    wanted = args.get("req_ids") or []
    if wanted:
        pool = set(wanted)
        requirements = [r for r in requirements if r["req_id"] in pool]

    resume_lower = resume.text.lower()
    resume_tokens = set(extract.tokens(resume.text))
    results: list[dict] = []

    for req in requirements:
        req_text = req["text"]
        keywords = req.get("keywords") or []
        core = [k for k in keywords if extract.is_tech_term(k)]
        matched = [k for k in core if _contains(resume_lower, k)]

        if core:
            if len(matched) == len(core):
                status = "covered"
            elif matched:
                status = "partial"
            else:
                status = "missing"
        else:
            req_tokens = set(extract.tokens(req_text))
            ratio = (len(req_tokens & resume_tokens) / max(1, len(req_tokens)))
            status = "covered" if ratio >= 0.6 else ("partial" if ratio >= 0.2 else "missing")

        hits = _search(resume, req_text, 3)
        evidence = _verified_evidence(resume, hits)
        confidence = {"covered": 0.9, "partial": 0.6, "missing": 0.25}[status]

        # 没有技术关键词的要求只看措辞重合度；能定位到原文时至少算部分命中
        if not core and status == "missing" and evidence:
            status = "partial"
            confidence = 0.4

        if status == "covered":
            reason = f"简历中命中 {', '.join(matched)}，且能定位到原文"
        elif status == "partial":
            missing = [k for k in core if k not in matched]
            if missing:
                reason = f"仅命中 {', '.join(matched)}，缺少 {', '.join(missing)}"
            elif core:
                reason = "关键词部分命中，需人工确认"
            else:
                reason = "简历有相关表述，但未覆盖要求中的关键动作，需人工确认"
        else:
            label = ", ".join(core or keywords) or (req_text[:18] + "…")
            reason = f"简历中未见 {label}"

        years = _YEARS_IN_REQ.search(req_text)
        if years and not _RESUME_YEARS.search(resume.text):
            status = "partial"
            confidence = 0.4
            reason = f"年限要求「{years.group(0)}」无法从简历自动确认（未见全职年限表述）"

        if status != "missing" and not evidence:
            status = "partial" if core else status
            confidence = min(confidence, 0.4)
            reason = "命中关键词但未能定位到可引用的原文，需人工确认"

        results.append({
            "req_id": req["req_id"],
            "requirement": req_text,
            "status": status,
            "evidence": evidence,
            "reason": reason,
            "confidence": confidence,
        })

    return ToolResult(True, data=results)


# ------------------------------------------------------------------ J5
def detect_new_facts(args: dict, ctx) -> ToolResult:
    resume = RESUMES.get(args.get("resume_id", ""))
    if resume is None:
        return ToolResult(False, error_code=SOURCE_NOT_FOUND, message="简历未加载")
    draft = args.get("draft_text") or ""
    base = extract.entity_keys(resume.entities)
    found = extract.extract_entities(draft)

    added: list[dict] = []
    for kind, items in found.items():
        pool = base.get(kind, set())
        for item in items:
            if extract.norm_value(kind, item["value"]) in pool:
                continue
            added.append({"entity": item["value"], "type": kind, "span": item["span"]})
    added.sort(key=lambda x: x["span"]["start"])
    return ToolResult(True, data={"added": added, "ok": not added})


# ------------------------------------------------------------------ J6
def save_application(args: dict, ctx) -> ToolResult:
    action = args.get("action", "save")
    if action == "list":
        return ToolResult(True, data={"items": applications.list_items()})

    jd = JDS.get(args.get("jd_id", ""))
    if jd is None:
        return ToolResult(False, error_code=SOURCE_NOT_FOUND, message="JD 未解析")
    application_id = applications.upsert(
        jd_id=jd.jd_id,
        title=jd.parsed.get("title", ""),
        company=jd.parsed.get("company", ""),
        status=(args.get("status") or "analyzed"),
        note=(args.get("note") or ""),
    )
    return ToolResult(True, data={"application_id": application_id, "jd_id": jd.jd_id})


# ------------------------------------------------------------------ J7
def _clip(text: str, limit: int = 36) -> str:
    text = (text or "").replace("|", "／").replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def render_brief(args: dict, ctx) -> ToolResult:
    results = ctx.state.get("results", {})
    jd = results.get("s1") or {}
    resume = results.get("s2") or {}
    matches = results.get("s3") or []
    analyst = results.get("s4") or {}
    review = results.get("s5") or {}
    application = results.get("s6") or {}

    summary = analyst.get("summary") or {}
    bullets = (review.get("draft") or {}).get("bullets") or []
    sections = {s["name"] for s in (resume.get("sections") or [])}
    requirement_map = {item.get("req_id", ""): item.get("requirement", "")
                       for item in matches}

    lines: list[str] = [
        f"# 投递决策卡：{jd.get('title', '') or '未识别岗位'}",
        "",
        f"- 公司：{jd.get('company', '') or '未识别'}",
        f"- 年限要求：{jd.get('years', '') or '未写明'}",
        f"- 来源：{jd.get('source_url', '') or '手工粘贴'}",
        f"- 简历：{(resume.get('stats') or {}).get('chars', 0)} 字 / "
        f"{(resume.get('stats') or {}).get('sections', 0)} 节",
        "",
        f"## 匹配矩阵（覆盖 {summary.get('covered', 0)} / 部分 {summary.get('partial', 0)}"
        f" / 缺少 {summary.get('missing', 0)}，共 {summary.get('total', 0)} 条）",
        "",
        "| 要求 | 判定 | 证据（简历原文） |",
        "| --- | --- | --- |",
    ]
    for item in matches:
        evidence = item.get("evidence") or []
        quote = evidence[0]["quote"] if evidence else "—"
        lines.append(f"| {_clip(item.get('requirement', ''), 30)} | {item.get('status', '')} "
                     f"| {_clip(quote, 32)} |")

    lines += ["", "## 缺口与选项", "", "| 缺口 | 严重度 | 选项 | 理由 |", "| --- | --- | --- | --- |"]
    gaps = analyst.get("gaps") or []
    for gap in gaps:
        lines.append(f"| {_clip(gap.get('gap', ''), 30)} | {gap.get('severity', '')} "
                     f"| {' / '.join(gap.get('options') or [])} | {_clip(gap.get('reason', ''), 40)} |")

    lines += ["", "## 改写建议（只改措辞，事实来自简历原文）", ""]
    for i, bullet in enumerate(bullets):
        lines.append(f"{i + 1}. {bullet.get('text', '')}")
        if bullet.get("source_text"):
            lines.append(f"   - 原文：{bullet['source_text']}")
        lines.append(f"   - 依据句子：{bullet.get('based_on_spans')}")
        requirement = requirement_map.get(bullet.get("req_id", ""), "")
        if requirement:
            lines.append(f"   - 对应要求：{_clip(requirement, 28)}")
        if bullet.get("source_text") and bullet.get("text") == bullet.get("source_text"):
            lines.append("   - 备注：原句已覆盖该要求，措辞无需改动")
    if not bullets:
        lines.append("（无可改写素材）")

    lines += ["", "## 审核结果", ""]
    lines.append(f"- 轮次：{review.get('rounds', 0)}")
    lines.append(f"- 结论：{'通过' if review.get('ok') else '未通过'}")
    for item in ctx.state.get("jobsearch_rejections", []):
        lines.append(f"- 打回：第 {item.get('round')} 轮 {item.get('reason_code')} "
                     f"{item.get('detail', '')}")
    if application.get("application_id"):
        lines.append(f"- 投递记录：{application['application_id']}")

    lines += ["", "## 面试问题（按缺口生成）", ""]
    questions: list[str] = []
    for gap in gaps:
        if len(questions) >= 5:
            break
        if not extract.keywords_of(gap.get("gap", "")):
            continue
        questions.append(f"针对「{_clip(gap.get('gap', ''), 24)}」，请举一个你亲手完成的例子，"
                         "说明当时的约束、你的动作和可量化的结果。")
    tech = [t for t in (jd.get("tech") or [])][:5]
    for term in tech:
        if len(questions) >= 5:
            break
        questions.append(f"你在项目里怎么用 {term}？遇到的最大问题是什么，怎么定位的？")
    while len(questions) < 5:
        questions.append("这个岗位要求的哪一项你目前最弱？你打算用什么材料补上？")
    for i, question in enumerate(questions[:5]):
        lines.append(f"{i + 1}. {question}")

    if summary and not sections:
        lines.append("")
        lines.append("> 注：简历未按标题分节，证据按段落定位。")

    body = "\n".join(lines) + "\n"
    config.EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = (args.get("run_id") or "").strip() or ctx.run_id
    path = config.EXPORTS_DIR / f"{run_id}-brief.md"
    path.write_text(body, encoding="utf-8")
    return ToolResult(True, data={"path": str(path), "bytes": len(body.encode("utf-8"))})


# ------------------------------------------------------------------ specs
TOOL_SPECS: list[tuple[ToolSpec, callable]] = [
    (ToolSpec(
        name="parse_jd",
        description="解析用户粘贴的 JD 文本，拆出 must-have / nice-to-have 与关键词；匹配前必须先调用",
        params={"type": "object", "required": ["raw_text"],
                "properties": {"raw_text": {"type": "string"},
                               "source_url": {"type": "string"}},
                "additionalProperties": False},
        returns={"type": "object"}, side_effect="none", timeout_s=10.0,
    ), parse_jd),
    (ToolSpec(
        name="load_resume",
        description="加载简历（text 或本地 path），按节切分并抽实体；分析前必须调用",
        params={"type": "object",
                "properties": {"text": {"type": "string"}, "path": {"type": "string"}},
                "additionalProperties": False},
        returns={"type": "object"}, timeout_s=30.0, max_calls_per_run=3,
    ), load_resume),
    (ToolSpec(
        name="search_resume",
        description="在简历中检索与查询最相关的原句，返回可引用的 span；需要举例或补证据时调用",
        params={"type": "object", "required": ["resume_id", "query"],
                "properties": {"resume_id": {"type": "string"},
                               "query": {"type": "string"},
                               "top_k": {"type": "integer"}},
                "additionalProperties": False},
        returns={"type": "object"}, timeout_s=10.0,
    ), search_resume),
    (ToolSpec(
        name="match_requirements",
        description="逐条判定 JD 要求为 covered/partial/missing，并为每条判定附简历原文证据",
        params={"type": "object", "required": ["jd_id", "resume_id"],
                "properties": {"jd_id": {"type": "string"},
                               "resume_id": {"type": "string"},
                               "req_ids": {"type": "array", "items": {"type": "string"}}},
                "additionalProperties": False},
        returns={"type": "array"}, side_effect="none", timeout_s=30.0,
    ), match_requirements),
    (ToolSpec(
        name="detect_new_facts",
        description="比对改写文本与简历实体，找出新增的机构/技术/数字/时间；审核改写时调用",
        params={"type": "object", "required": ["draft_text", "resume_id"],
                "properties": {"draft_text": {"type": "string"},
                               "resume_id": {"type": "string"}},
                "additionalProperties": False},
        returns={"type": "object"}, side_effect="none", timeout_s=10.0,
    ), detect_new_facts),
    (ToolSpec(
        name="save_application",
        description="把当前 JD 写入本地投递记录（同库新表），或用 action=list 列出现有记录",
        params={"type": "object", "required": ["action"],
                "properties": {"action": {"type": "string", "enum": ["save", "list"]},
                               "jd_id": {"type": "string"},
                               "status": {"type": "string"},
                               "note": {"type": "string"}},
                "additionalProperties": False},
        returns={"type": "object"}, side_effect="local_write",
        timeout_s=10.0, max_calls_per_run=3,
    ), save_application),
    (ToolSpec(
        name="render_brief",
        description="把匹配矩阵、缺口、改写与审核结果写成一页 Markdown 投递决策卡",
        params={"type": "object", "required": ["run_id"],
                "properties": {"run_id": {"type": "string"},
                               "fmt": {"type": "string", "enum": ["md"]}},
                "additionalProperties": False},
        returns={"type": "object"}, side_effect="local_write",
        timeout_s=30.0, max_calls_per_run=1,
    ), render_brief),
]
