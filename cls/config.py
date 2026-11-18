"""本地配置：路径、Agent 预算与可选的本地模型地址。

展示版只保留求职场景用到的项：合同阈值、行业开关、授权状态都不在这里。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

RESOURCE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = RESOURCE_DIR / "data"

# 多实例隔离：CLS_DATA_DIR 可把 trace、投递卡、sqlite 记忆指向独立目录，
# 便于在同一台机器上并行跑多个实例，互不污染。
_data_override = os.environ.get("CLS_DATA_DIR", "").strip()
if _data_override:
    DATA_DIR = Path(_data_override).expanduser().resolve()

BASE_DIR = RESOURCE_DIR
SAMPLES_DIR = RESOURCE_DIR / "samples"
EXPORTS_DIR = DATA_DIR / "exports"
CONFIG_PATH = DATA_DIR / "config.json"
DB_PATH = DATA_DIR / "history.db"

_CONFIG_LOCK = threading.Lock()

DEFAULT_CONFIG = {
    "ollama_url": os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
    "model": os.environ.get("OLLAMA_MODEL", "qwen2.5:3b"),
    "llm_timeout": 180,
    # Agent 运行时：默认关闭。展示版没有把模型接进判定链路，
    # 这里只保留预算，供 AgentRuntime 读取。
    "agent": {
        "enabled": False,
        "model": "",
        "budget": {
            "max_steps": 20,
            "max_tool_calls": 60,
            "max_wall_s": 180,
            "max_tokens": 20000,
        },
    },
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            nested = {k: saved.pop(k) for k in ("agent",) if k in saved}
            cfg.update(saved)
            for key, value in nested.items():
                if isinstance(value, dict):
                    merged = dict(DEFAULT_CONFIG[key])
                    merged.update(value)
                    if isinstance(value.get("budget"), dict):
                        # 预算允许只覆盖部分字段，其余沿用默认值
                        budget = dict(DEFAULT_CONFIG["agent"]["budget"])
                        budget.update(value["budget"])
                        merged["budget"] = budget
                    cfg[key] = merged
        except Exception:
            # 配置损坏时备份原始文件再回退默认值，避免下一次保存把用户内容彻底覆盖
            try:
                CONFIG_PATH.replace(CONFIG_PATH.with_name(CONFIG_PATH.name + ".broken"))
            except OSError:
                pass
    return cfg


def save_config(patch: dict) -> dict:
    with _CONFIG_LOCK:
        cfg = load_config()
        for key in ("ollama_url", "model", "llm_timeout"):
            if key in patch:
                cfg[key] = patch[key]
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, CONFIG_PATH)
        return cfg


for _d in (DATA_DIR, EXPORTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
