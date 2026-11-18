"""进程内 JD / 简历暂存：只保留正文与解析结果，不落盘、不进 trace。"""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field


def _sha8(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:8]


@dataclass
class JDRecord:
    jd_id: str
    raw_text: str
    source_url: str = ""
    parsed: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)


@dataclass
class ResumeRecord:
    resume_id: str
    text: str
    name_sha8: str = ""
    sections: list = field(default_factory=list)
    sentences: list = field(default_factory=list)
    entities: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)


class JDStore:
    def __init__(self) -> None:
        self._items: dict[str, JDRecord] = {}
        self._lock = threading.RLock()

    def add(self, raw_text: str, source_url: str = "") -> str:
        jd_id = "jd-" + _sha8(raw_text)
        with self._lock:
            if jd_id not in self._items:
                self._items[jd_id] = JDRecord(jd_id=jd_id, raw_text=raw_text or "",
                                              source_url=source_url or "")
        return jd_id

    def get(self, jd_id: str) -> JDRecord | None:
        with self._lock:
            return self._items.get(jd_id or "")

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


class ResumeStore:
    def __init__(self) -> None:
        self._items: dict[str, ResumeRecord] = {}
        self._lock = threading.RLock()

    def add(self, text: str, filename: str = "") -> str:
        resume_id = "cv-" + _sha8(text)
        with self._lock:
            if resume_id not in self._items:
                self._items[resume_id] = ResumeRecord(
                    resume_id=resume_id, text=text or "", name_sha8=_sha8(filename))
        return resume_id

    def get(self, resume_id: str) -> ResumeRecord | None:
        with self._lock:
            return self._items.get(resume_id or "")

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


JDS = JDStore()
RESUMES = ResumeStore()
