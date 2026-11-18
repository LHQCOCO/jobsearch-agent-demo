"""注册表：参数校验、错误码、预算与超时。"""
from __future__ import annotations

import time

import pytest

from cls.agent.context import RunContext
from cls.agent.registry import ToolRegistry
from cls.agent.trace import Trace
from cls.agent.types import Budget, ToolResult, ToolSpec


def make_ctx(tmp_path, budget=None):
    return RunContext("r1", Trace(tmp_path / "t.jsonl", "r1"), budget or Budget())


def ok_tool(args, ctx):
    return ToolResult(True, data={"echo": args.get("x")})


SPEC = ToolSpec(
    name="echo",
    description="回显",
    params={
        "type": "object",
        "required": ["x"],
        "properties": {"x": {"type": "string"},
                       "mode": {"type": "string", "enum": ["a", "b"]}},
        "additionalProperties": False,
    },
)


def test_register_and_list():
    reg = ToolRegistry()
    reg.register(SPEC, ok_tool)
    assert [s.name for s in reg.specs()] == ["echo"]


def test_duplicate_register_rejected():
    reg = ToolRegistry()
    reg.register(SPEC, ok_tool)
    with pytest.raises(ValueError):
        reg.register(SPEC, ok_tool)


def test_unknown_tool_returns_code(tmp_path):
    reg = ToolRegistry()
    res = reg.call("nope", {}, make_ctx(tmp_path))
    assert res.ok is False and res.error_code == "TOOL_NOT_FOUND"


def test_missing_required_field(tmp_path):
    reg = ToolRegistry()
    reg.register(SPEC, ok_tool)
    res = reg.call("echo", {}, make_ctx(tmp_path))
    assert res.error_code == "INVALID_ARGS"
    assert res.data["details"][0]["path"].endswith(".x")


def test_wrong_type(tmp_path):
    reg = ToolRegistry()
    reg.register(SPEC, ok_tool)
    res = reg.call("echo", {"x": 1}, make_ctx(tmp_path))
    assert res.error_code == "INVALID_ARGS"


def test_enum_out_of_range(tmp_path):
    reg = ToolRegistry()
    reg.register(SPEC, ok_tool)
    res = reg.call("echo", {"x": "ok", "mode": "c"}, make_ctx(tmp_path))
    assert res.error_code == "INVALID_ARGS"


def test_extra_key_not_silently_dropped(tmp_path):
    reg = ToolRegistry()
    reg.register(SPEC, ok_tool)
    res = reg.call("echo", {"x": "ok", "合同原文": "正文"}, make_ctx(tmp_path))
    assert res.error_code == "INVALID_ARGS"
    assert any("未声明" in e["message"] for e in res.data["details"])


def test_timeout_returns_without_waiting(tmp_path):
    reg = ToolRegistry()
    reg.register(ToolSpec(name="slow", description="慢", params={"type": "object"},
                          timeout_s=0.2),
                 lambda a, c: (time.sleep(0.8), ToolResult(True))[1])
    started = time.perf_counter()
    res = reg.call("slow", {}, make_ctx(tmp_path))
    assert res.error_code == "TOOL_TIMEOUT"
    assert time.perf_counter() - started < 0.6


def test_per_tool_call_cap(tmp_path):
    reg = ToolRegistry()
    spec = ToolSpec(name="capped", description="限次", params={"type": "object"},
                    max_calls_per_run=2)
    reg.register(spec, ok_tool)
    ctx = make_ctx(tmp_path)
    assert reg.call("capped", {}, ctx).ok
    assert reg.call("capped", {}, ctx).ok
    assert reg.call("capped", {}, ctx).error_code == "TOOL_BUDGET_EXCEEDED"


def test_global_tool_budget(tmp_path):
    reg = ToolRegistry()
    reg.register(SPEC, ok_tool)
    ctx = make_ctx(tmp_path, Budget(max_tool_calls=1))
    assert reg.call("echo", {"x": "1"}, ctx).ok
    assert reg.call("echo", {"x": "2"}, ctx).error_code == "TOOL_BUDGET_EXCEEDED"


def test_tool_exception_becomes_error_code(tmp_path):
    reg = ToolRegistry()
    reg.register(SPEC, lambda a, c: (_ for _ in ()).throw(RuntimeError("boom")))
    res = reg.call("echo", {"x": "ok"}, make_ctx(tmp_path))
    assert res.error_code == "TOOL_ERROR"
    assert "boom" not in res.message
