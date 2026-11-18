"""Ollama 本机探测：只访问 127.0.0.1 / localhost，绝不连接外部服务。

展示版只保留 ``status()``，供 ``cls/agent/adapter.py`` 判断模型是否可用；
字段抽取、条款复核这些合同场景的函数没有带过来。
"""
from __future__ import annotations

from urllib.parse import urlparse

import requests

from . import config


class LocalModelError(RuntimeError):
    pass


def _is_local(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def _norm_model(name: str) -> str:
    """ollama 列表省略 :latest 后缀，比较前统一补齐。"""
    return name if ":" in name else f"{name}:latest"


def status(url: str | None = None, model: str | None = None) -> dict:
    cfg = config.load_config()
    url = (url or cfg["ollama_url"]).rstrip("/")
    model = model or cfg["model"]
    out = {
        "url": url,
        "model": model,
        "local_endpoint": _is_local(url),
        "online": False,
        "models": [],
        "model_ready": False,
        "error": "",
    }
    if not _is_local(url):
        out["error"] = "出于隐私策略，仅允许连接本机地址（127.0.0.1 / localhost）。"
        return out
    try:
        # 只连本机：禁用环境变量里的代理，并禁止重定向
        r = requests.get(f"{url}/api/tags", timeout=4,
                         proxies={"http": None, "https": None}, allow_redirects=False)
        r.raise_for_status()
        models = [m.get("name", "") for m in r.json().get("models", [])]
        out["online"] = True
        out["models"] = models
        out["model_ready"] = any(_norm_model(m) == _norm_model(model) for m in models)
        if not out["model_ready"]:
            out["error"] = f"本机未找到模型 {model}，请先执行 ollama pull {model}"
    except Exception as e:  # noqa: BLE001
        out["error"] = f"未连接到本地 Ollama：{e.__class__.__name__}"
    return out
