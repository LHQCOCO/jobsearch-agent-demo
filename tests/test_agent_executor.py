"""执行器：依赖、重试、foreach、预算停止。"""
from __future__ import annotations

import json

from cls.agent.context import RunContext
from cls.agent.executor import Executor
from cls.agent.registry import ToolRegistry
from cls.agent.trace import Trace
from cls.agent.types import Budget, Plan, Step, ToolResult, ToolSpec


def make_ctx(tmp_path, budget=None):
    trace = Trace(tmp_path / "t.jsonl", "r1")
    return RunContext("r1", trace, budget or Budget(), state={}), trace


def simple_registry():
    reg = ToolRegistry()
    reg.register(ToolSpec(name="ok", description="成功", params={"type": "object"}),
                 lambda a, c: ToolResult(True, data={"value": a.get("v", 1)}))
    reg.register(ToolSpec(name="fail", description="失败", params={"type": "object"}),
                 lambda a, c: ToolResult(False, error_code="TOOL_ERROR"))
    reg.register(ToolSpec(name="list", description="列表", params={"type": "object"}),
                 lambda a, c: ToolResult(True, data={"items": [{"n": 1}, {"n": 2}, {"n": 3}]}))
    return reg


def test_foreach_expands_to_substeps(tmp_path):
    ctx, _ = make_ctx(tmp_path)
    plan = Plan(steps=[
        Step("s1", "", tool="list", args={}),
        Step("s2", "", tool="ok", args={"v": "{item.n}"},
             depends_on=("s1",), foreach="s1.items"),
    ])
    result = Executor(simple_registry()).run({}, plan, ctx)
    assert result.status == "ok"
    assert result.artifacts["s2[0]"]["value"] == 1
    assert result.artifacts["s2[2]"]["value"] == 3


def test_foreach_truncates_and_records(tmp_path):
    ctx, trace = make_ctx(tmp_path)
    plan = Plan(steps=[
        Step("s1", "", tool="list", args={}),
        Step("s2", "", tool="ok", args={"v": "{item.n}"},
             depends_on=("s1",), foreach="s1.items"),
    ])
    Executor(simple_registry(), max_foreach_items=2).run({}, plan, ctx)
    assert "s2[2]" not in ctx.state["results"]
    assert any(e.get("code") == "TRUNCATED" for e in trace.dump())


def test_dependency_skipped_when_upstream_fails(tmp_path):
    ctx, trace = make_ctx(tmp_path)
    plan = Plan(steps=[
        Step("s1", "", tool="fail", args={}),
        Step("s2", "", tool="ok", args={}, depends_on=("s1",)),
    ])
    result = Executor(simple_registry()).run({}, plan, ctx)
    assert "s2" not in result.artifacts
    assert any(e.get("error_code") == "SKIPPED_DEP" for e in trace.dump())


def test_retry_then_success(tmp_path):
    ctx, _ = make_ctx(tmp_path)
    calls = {"n": 0}

    def flaky(args, ctx):
        calls["n"] += 1
        if calls["n"] == 1:
            return ToolResult(False, error_code="TOOL_ERROR")
        return ToolResult(True, data={"ok": True})

    reg = simple_registry()
    reg.register(ToolSpec(name="flaky", description="抖动", params={"type": "object"}), flaky)
    plan = Plan(steps=[Step("s1", "", tool="flaky", args={}, max_attempts=2)])
    result = Executor(reg).run({}, plan, ctx)
    assert result.status == "ok" and calls["n"] == 2


def test_retry_exhausted_continues_independent_step(tmp_path):
    ctx, _ = make_ctx(tmp_path)
    plan = Plan(steps=[
        Step("s1", "", tool="fail", args={}, max_attempts=2),
        Step("s2", "", tool="ok", args={}),
    ])
    result = Executor(simple_registry()).run({}, plan, ctx)
    assert result.status == "partial"
    assert "s1" not in result.artifacts and "s2" in result.artifacts


def test_foreach_shares_one_step_of_budget(tmp_path):
    """foreach 展开的是工作量，不是计划步骤：max_steps 只按 Step 计数。"""
    ctx, _ = make_ctx(tmp_path, Budget(max_steps=2))
    plan = Plan(steps=[
        Step("s1", "", tool="list", args={}),
        Step("s2", "", tool="ok", args={"v": "{item.n}"},
             depends_on=("s1",), foreach="s1.items"),
    ])
    result = Executor(simple_registry()).run({}, plan, ctx)
    assert result.status == "ok"
    assert result.artifacts["s2[2]"]["value"] == 3
    assert result.metrics["steps"] == 2


def test_budget_stops_and_keeps_artifacts(tmp_path):
    ctx, trace = make_ctx(tmp_path, Budget(max_steps=1))
    plan = Plan(steps=[
        Step("s1", "", tool="ok", args={}),
        Step("s2", "", tool="ok", args={}),
    ])
    result = Executor(simple_registry()).run({}, plan, ctx)
    assert result.stop_reason == "BUDGET_EXCEEDED"
    assert result.status == "partial"
    assert "s1" in result.artifacts and "s2" not in result.artifacts
    events = trace.dump()
    assert json.dumps(events).find("BUDGET_EXCEEDED") >= 0


def test_invalid_plan_reports_plan_invalid(tmp_path):
    ctx, trace = make_ctx(tmp_path)
    plan = Plan(steps=[
        Step("s1", "", tool="ok", args={}),
        Step("s2", "", tool="ok", args={}, depends_on=("s9",)),
    ])
    result = Executor(simple_registry()).run({}, plan, ctx)
    assert result.stop_reason == "PLAN_INVALID"
    assert result.status == "failed" and not result.artifacts
    assert any(e.get("stop_reason") == "PLAN_INVALID" for e in trace.dump())
