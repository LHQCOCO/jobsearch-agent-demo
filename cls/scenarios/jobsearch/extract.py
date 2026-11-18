"""JD 与简历的规则解析：切段、拆要求、抽实体。

全部确定性实现，不调用模型：
- 要求（must/nice）按章节标题与"优先/加分"字样切分；
- 简历按 Markdown 标题分节，无标题时按空行分块；
- 实体只抽四类：机构、技术、数字、时间，全部带 offset，供证据层比对。
"""
from __future__ import annotations

import math
import re

TECH_TERMS: tuple[str, ...] = (
    "Python", "Java", "Go", "C++", "JavaScript", "TypeScript",
    "SQL", "MySQL", "PostgreSQL", "Redis", "MongoDB", "Elasticsearch",
    "Kafka", "Spark", "Hive", "ClickHouse", "Docker", "Kubernetes", "K8s",
    "Linux", "Git", "Flask", "FastAPI", "Django", "Spring", "React", "Vue",
    "PyTorch", "TensorFlow", "Transformer", "BERT", "LLM", "GPT", "RAG",
    "Agent", "LangChain", "LlamaIndex", "Milvus", "FAISS", "Pinecone",
    "Chroma", "vLLM", "Ollama", "BM25", "OCR", "Prompt", "Function Calling",
    "Tool Use", "Fine-tuning", "SFT", "DPO", "PDF", "DOCX",
)

DOMAIN_TERMS: tuple[str, ...] = (
    "检索", "评测", "向量", "知识库", "部署", "微调", "工作流", "数据处理",
    "后端", "前端", "算法", "架构", "文档解析", "多智能体", "上下文", "规划",
    "工具调用", "单元测试", "提示词",
)

_TECH_LOWER = {t.lower(): t for t in TECH_TERMS}


def _pattern(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])", re.I)


_TECH_PATTERNS = [(t, _pattern(t)) for t in TECH_TERMS]

_METRIC_RE = re.compile(
    r"\d[\d,]*(?:\.\d+)?\s*(?:%|％|万|亿|千|倍|人|次|份|条|个|页|行|小时|分钟|秒|ms|GB|MB|QPS|k|K)")
_DATE_RE = re.compile(
    r"(?:19|20)\d{2}\s*(?:年(?:\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?)?|[./-]\d{1,2}(?:[./-]\d{1,2})?)?"
    r"|\d{1,2}\s*年(?:以上)?(?:工作)?经验")
_ORG_TAIL = re.compile(r"(?:股份有限公司|有限公司|集团|研究院|研究所|大学|学院|实验室)")
_YEARS_RANGE = re.compile(r"(\d{1,2})\s*[-~－—至]\s*(\d{1,2})\s*年")
_YEARS_ABOVE = re.compile(r"(\d{1,2})\s*年(?:及)?以上")
_YEARS_EXP = re.compile(r"(\d{1,2})\s*年.{0,6}经验")

_SPLIT_RE = re.compile(r"[。；;\n]+")


# ------------------------------------------------------------------ tokenizing
def tokens(text: str) -> list[str]:
    """中文双字切片 + 拉丁词。与内核记忆召回的口径一致，便于两处复用同一思路。"""
    text = (text or "").lower()
    grams: list[str] = re.findall(r"[a-z][a-z0-9+#.]{1,}", text)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) == 1:
            grams.append(run)
        for i in range(len(run) - 1):
            grams.append(run[i:i + 2])
    return grams


def score(query: str, text: str) -> float:
    """TF / 长度归一：短段落命中同一关键词时得分更高，避免长文本凭篇幅占优。"""
    q = set(tokens(query))
    terms = tokens(text)
    if not q or not terms:
        return 0.0
    tf: dict[str, int] = {}
    for t in terms:
        tf[t] = tf.get(t, 0) + 1
    hit = sum(min(tf.get(t, 0), 3) for t in q)
    if not hit:
        return 0.0
    return hit / math.sqrt(len(terms))


# ------------------------------------------------------------------ entities
def find_tech(text: str) -> list[dict]:
    text = text or ""
    found: list[dict] = []
    for canonical, pattern in _TECH_PATTERNS:
        for m in pattern.finditer(text):
            found.append({"value": canonical,
                          "span": {"start": m.start(), "end": m.end()}})
    found.sort(key=lambda x: x["span"]["start"])
    return found


def find_orgs(text: str) -> list[dict]:
    text = text or ""
    out: list[dict] = []
    for m in _ORG_TAIL.finditer(text):
        start = max(0, m.start() - 24)
        segment = text[start:m.end()]
        pieces = re.split(r"[\s，,。；;、:：|/（）()【】\[\]“”\"]", segment)
        cand = (pieces[-1] if pieces else segment).strip()
        if len(cand) < 4:
            continue
        pos = text.rfind(cand, start, m.end() + 1)
        if pos < 0:
            pos = m.end() - len(cand)
        out.append({"value": cand, "span": {"start": pos, "end": pos + len(cand)}})
    return out


def _norm_metric(value: str) -> str:
    return re.sub(r"\s+", "", value).replace(",", "").replace("％", "%").lower()


def _norm_date(value: str) -> str:
    digits = re.findall(r"\d+", value)
    if not digits:
        return value.strip()
    year = digits[0]
    if len(digits) == 1:
        return f"{year}y" if "年" in value and len(year) <= 2 else year
    month = digits[1].zfill(2)
    if len(digits) == 2:
        return f"{year}-{month}"
    return f"{year}-{month}-{digits[2].zfill(2)}"


def _norm_tech(value: str) -> str:
    return re.sub(r"\s+", "", value.lower())


def _norm_org(value: str) -> str:
    return re.sub(r"\s+", "", value)


def extract_entities(text: str) -> dict[str, list[dict]]:
    text = text or ""
    out: dict[str, list[dict]] = {"org": [], "tech": [], "metric": [], "date": []}
    seen: dict[str, set[str]] = {k: set() for k in out}

    def push(kind: str, raw: str, span: dict, norm) -> None:
        key = norm(raw)
        if not key or key in seen[kind]:
            return
        seen[kind].add(key)
        out[kind].append({"value": raw, "span": span})

    for item in find_orgs(text):
        push("org", item["value"], item["span"], _norm_org)
    for item in find_tech(text):
        push("tech", item["value"], item["span"], _norm_tech)
    for m in _METRIC_RE.finditer(text):
        push("metric", m.group(0).strip(), {"start": m.start(), "end": m.end()}, _norm_metric)
    for m in _DATE_RE.finditer(text):
        push("date", m.group(0).strip(), {"start": m.start(), "end": m.end()}, _norm_date)
    return out


_NORMS = {"org": _norm_org, "tech": _norm_tech, "metric": _norm_metric, "date": _norm_date}


def norm_value(kind: str, value: str) -> str:
    return _NORMS.get(kind, lambda v: (v or "").strip())(value)


def is_tech_term(term: str) -> bool:
    return (term or "").lower() in _TECH_LOWER


def entity_keys(entities: dict) -> dict[str, set[str]]:
    return {kind: {norm_value(kind, e["value"]) for e in items}
            for kind, items in (entities or {}).items() if kind in _NORMS}


# ------------------------------------------------------------------ resume
def split_sections(text: str) -> list[dict]:
    text = text or ""
    marks: list[tuple[int, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        m = re.match(r"^#{1,6}\s*(.+?)\s*$", line.strip())
        if m:
            marks.append((offset, m.group(1)))
        offset += len(line)

    if not marks:
        sections: list[dict] = []
        pos = 0
        for block in re.split(r"(\n\s*\n)", text):
            if block.strip():
                sections.append({"name": f"段落{len(sections) + 1}",
                                 "span": {"start": pos, "end": pos + len(block)},
                                 "text": block})
            pos += len(block)
        return sections

    sections = []
    if marks[0][0] > 0 and text[:marks[0][0]].strip():
        sections.append({"name": "概述", "span": {"start": 0, "end": marks[0][0]},
                         "text": text[:marks[0][0]]})
    for i, (start, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        sections.append({"name": name, "span": {"start": start, "end": end},
                         "text": text[start:end]})
    return sections


def split_sentences(text: str, base: int = 0) -> list[dict]:
    out: list[dict] = []
    cursor = 0
    for m in _SPLIT_RE.finditer(text or ""):
        _push_sentence(out, text[cursor:m.start()], base + cursor)
        cursor = m.end()
    _push_sentence(out, (text or "")[cursor:], base + cursor)
    return out


def _push_sentence(out: list[dict], segment: str, offset: int) -> None:
    body = (segment or "").strip()
    # 标题行（# / ##）与过短片段不作为证据句
    if len(body) < 6 or body.startswith("#"):
        return
    body = _BULLET_RE.sub("", body).strip()
    if len(body) < 6:
        return
    start = offset + segment.find(body)
    out.append({"text": body, "span": {"start": start, "end": start + len(body)}})


def resume_sentences(sections: list[dict]) -> list[dict]:
    sentences: list[dict] = []
    for section in sections or []:
        for sent in split_sentences(section["text"], section["span"]["start"]):
            sent["section"] = section["name"]
            sentences.append(sent)
    return sentences


# ------------------------------------------------------------------ JD
_BULLET_RE = re.compile(r"^\s*(?:[-*•·]|\d{1,2}[.、)）])\s*")
_TITLE_RE = re.compile(r"(工程师|开发|研发|算法|产品|分析师|科学家|架构师|研究员|负责人)")
_COMPANY_RE = re.compile(r"(公司|集团|银行|研究院|研究所|中心|科技)")
_REQUIREMENT_RE = re.compile(r"(经验|熟悉|掌握|精通|了解|具备|负责|参与|能|熟练|本科|硕士|学历)")


def _section_kind(line: str) -> str:
    body = re.sub(r"^[#*\s【\[（(]+", "", line or "").strip()
    if body.startswith("加分") or body.startswith("优先"):
        return "nice"
    if any(body.startswith(k) or k in body[:10] for k in
           ("岗位职责", "工作职责", "职位职责", "工作内容", "职责描述", "你将负责")):
        return "responsibilities"
    if any(body.startswith(k) or k in body[:10] for k in
           ("任职要求", "岗位要求", "职位要求", "任职资格", "能力要求", "我们希望你", "任职条件")):
        return "must"
    return ""


def keywords_of(text: str) -> list[str]:
    found = [item["value"] for item in find_tech(text)]
    for term in DOMAIN_TERMS:
        if term in text:
            found.append(term)
    out: list[str] = []
    for item in found:
        if item not in out:
            out.append(item)
    return out


def detect_years(text: str) -> str:
    m = _YEARS_RANGE.search(text or "")
    if m:
        return f"{m.group(1)}-{m.group(2)}年"
    m = _YEARS_ABOVE.search(text or "")
    if m:
        return f"{m.group(1)}年以上"
    m = _YEARS_EXP.search(text or "")
    if m:
        return f"{m.group(1)}年"
    return ""


def parse_jd(raw_text: str, source_url: str = "") -> dict:
    text = raw_text or ""
    lines = [line.strip().lstrip("#").strip() for line in text.splitlines() if line.strip()]
    current = ""
    must: list[str] = []
    nice: list[str] = []
    responsibilities: list[str] = []
    title = ""
    company = ""

    for line in lines:
        kind = _section_kind(line)
        if kind and len(line) <= 40:
            current = kind
            continue
        body = _BULLET_RE.sub("", line).strip()
        if not title and len(body) <= 40 and _TITLE_RE.search(body):
            title = body
        if not company and len(body) <= 40 and _COMPANY_RE.search(body) \
                and not _REQUIREMENT_RE.search(body):
            company = re.split(r"[·|｜]", body)[0].strip()
        if len(body) < 4:
            continue
        if current == "responsibilities":
            responsibilities.append(body)
        elif current == "nice":
            nice.append(body)
        elif current == "must":
            must.append(body)
        elif _REQUIREMENT_RE.search(body):
            must.append(body)

    if not title and lines:
        title = _BULLET_RE.sub("", lines[0]).lstrip("# ").strip()

    def build(items: list[str], start: int) -> list[dict]:
        return [{"req_id": f"req-{start + i:02d}", "text": item,
                 "keywords": keywords_of(item)} for i, item in enumerate(items)]

    must_have = build(must, 1)
    nice_to_have = build(nice, 1 + len(must_have))

    return {
        "title": title,
        "company": company,
        "years": detect_years(text),
        "must_have": must_have,
        "nice_to_have": nice_to_have,
        "tech": keywords_of(text),
        "responsibilities": responsibilities,
        "source_url": source_url or "",
    }
