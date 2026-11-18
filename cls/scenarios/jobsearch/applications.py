"""投递记录：与现有 history.db 同库新表，不改动 reviews / jobs 两张旧表。"""
from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path

from ... import config

_TABLE = """CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY,
    jd_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    company TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'analyzed',
    note TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
)"""


def _conn(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(_TABLE)
    return conn


def upsert(jd_id: str, title: str, company: str, status: str,
           note: str = "", db_path: str | Path | None = None) -> str:
    now = int(time.time() * 1000)
    with _conn(db_path) as conn:
        row = conn.execute("SELECT id FROM applications WHERE jd_id=?", (jd_id,)).fetchone()
        if row:
            conn.execute(
                "UPDATE applications SET title=?, company=?, status=?, note=?, updated_at=?"
                " WHERE jd_id=?", (title, company, status, note, now, jd_id))
            return str(row[0])
        app_id = uuid.uuid4().hex[:12]
        conn.execute(
            "INSERT INTO applications (id, jd_id, title, company, status, note, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (app_id, jd_id, title, company, status, note, now, now))
        return app_id


def list_items(limit: int = 50, db_path: str | Path | None = None) -> list[dict]:
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT jd_id, title, company, status, note, updated_at FROM applications"
            " ORDER BY updated_at DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
    keys = ["jd_id", "title", "company", "status", "note", "updated_at"]
    return [dict(zip(keys, row)) for row in rows]
