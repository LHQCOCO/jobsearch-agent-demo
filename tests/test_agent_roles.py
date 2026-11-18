"""角色：越权拦截、顺序管线、打回循环。"""
from __future__ import annotations

import pytest

from cls.agent.context import RunContext
from cls.agent.registry import ToolRegistry
from cls.agent.roles import (
    DeterministicRunner, Pipeline, ReviewLoop, Role, call_tool_for_role,
)
from cls.agent.trace import Trace
from cls.agent.types import Budget, ToolResult, ToolSpec


def make_ctx(tmp_path):
    return RunContext("r1", Trace(tmp_path / "t.jsonl", "r1"), Budget(), state={})


def registry():
    reg = ToolRegistry()
    reg.register(ToolSpec(name="C1", description="规则", params={"type": "object"}),
                 lambda a, c: ToolResult(True, data={"findings": []}))
    reg.register(ToolSpec(name="C2", description="定位", params={"type": "object"}),
                 lambda a, c: ToolResult(True, data={"matches": []}))
    return reg


def test_unauthorized_tool_blocked(tmp_path):
    role = Role("reviewer", "x", ("C1",), {}, 700)
    res = call_tool_for_role(role, registry(), "C2", {}, make_ctx(tmp_path))
    assert res.error_code == "UNAUTHORIZED_TOOL"


def test_pipeline_passes_previous_output(tmp_path):
    seen = []

    def producer(inputs, ctx):
        seen.append("producer")
        return {"value": 1}

    def verifier(inputs, ctx):
        seen.append(inputs.get("previous", {}).get("value"))
        return {"value": 2}

    runner = DeterministicRunner({"p": producer, "v": verifier})
    out = Pipeline(("p", "v")).run(runner, make_ctx(tmp_path), {})
    assert seen == ["producer", 1] and out["value"] == 2


def test_review_loop_rejects_then_passes(tmp_path):
    calls = {"n": 0}

    def producer(inputs, ctx):
        calls["n"] += 1
        return {"draft": calls["n"]}

    def verifier(inputs, ctx):
        if calls["n"] == 1:
            return {"verdicts": [{"rule_id": "R1", "verdict": "reject",
                                  "reason_code": "EVIDENCE_MISSING"}]}
        return {"verdicts": [{"rule_id": "R1", "verdict": "confirmed"}]}

    result = ReviewLoop("p", "v", max_rejections=2).run(
        DeterministicRunner({"p": producer, "v": verifier}),
        make_ctx(tmp_path), {})
    assert result["ok"] is True and result["rounds"] == 2


def test_review_loop_exhausts(tmp_path):
    verifier = lambda i, c: {"verdicts": [{"rule_id": "R1", "verdict": "reject",
                                           "reason_code": "CITATION_INVALID"}]}
    result = ReviewLoop("p", "v", max_rejections=1).run(
        DeterministicRunner({"p": lambda i, c: {"x": 1}, "v": verifier}),
        make_ctx(tmp_path), {})
    assert result["ok"] is False and result["rounds"] == 2


def test_invalid_reason_code_raises(tmp_path):
    verifier = lambda i, c: {"verdicts": [{"rule_id": "R1", "verdict": "reject",
                                           "reason_code": "WHATEVER"}]}
    with pytest.raises(ValueError):
        ReviewLoop("p", "v").run(
            DeterministicRunner({"p": lambda i, c: {}, "v": verifier}),
            make_ctx(tmp_path), {})
