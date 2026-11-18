"""证据比对与 trace 脱敏。"""
from __future__ import annotations

import pytest

from cls.agent.evidence import EvidenceChecker
from cls.agent.trace import Trace

DOC = "第一条 本合同自双方签字之日起生效。\n第二条 乙方应在收到发票后九十日内付款。"


def test_span_ok():
    quote = "乙方应在收到发票后九十日内付款"
    res = EvidenceChecker.check_span(DOC, quote, offset=DOC.find(quote))
    assert res.ok and res.code == "OK"
    assert DOC[res.span["start"]:res.span["end"]] == quote


def test_span_mismatch():
    res = EvidenceChecker.check_span(DOC, "甲方应一次性付款")
    assert not res.ok and res.code == "SPAN_MISMATCH"


def test_offset_drift():
    quote = "乙方应在收到发票后九十日内付款"
    res = EvidenceChecker.check_span(DOC, quote, offset=DOC.find(quote) + 10)
    assert not res.ok and res.code == "OFFSET_DRIFT"


def test_span_tolerates_whitespace():
    res = EvidenceChecker.check_span("甲 方 应 及 时 付 款", "甲方应及时付款")
    assert res.ok


def test_empty_quote():
    assert EvidenceChecker.check_span(DOC, "  ").code == "SPAN_MISMATCH"


def test_source_and_membership():
    assert EvidenceChecker.check_source("a", lambda x: {"a": 1}).ok
    assert EvidenceChecker.check_source("b", lambda x: None).code == "SOURCE_NOT_FOUND"
    assert EvidenceChecker.check_membership("x", ["x", "y"]).ok
    assert EvidenceChecker.check_membership("z", ["x"]).code == "NOT_IN_CANDIDATES"


def test_trace_fields_are_whitelisted_and_redacted(tmp_path):
    trace = Trace(tmp_path / "t.jsonl", "r1")
    trace.event("tool.call", tool="C1", args={"doc_id": "abcd1234"},
                quote="合同机密条款" * 20, path="/Users/someone/contract.pdf",
                unknown_field="应被丢弃")
    event = trace.dump()[0]
    assert event["seq"] == 1 and event["tool"] == "C1"
    assert len(event["args_digest"]) == 8
    assert "unknown_field" not in event
    assert "/Users" not in str(event) and "合同机密条款" not in str(event)
    assert event["quote_sha8"] and event["path_sha8"]


def test_trace_sequence_and_unknown_event(tmp_path):
    trace = Trace(tmp_path / "t.jsonl", "r1")
    trace.event("step.start", step_id="s1")
    trace.event("step.end", step_id="s1", ok=True)
    assert [e["seq"] for e in trace.dump()] == [1, 2]
    with pytest.raises(ValueError):
        trace.event("illegal.event")


def test_trace_span_writes_latency(tmp_path):
    trace = Trace(tmp_path / "t.jsonl", "r1")
    with trace.span("verify.result", role="verifier"):
        pass
    event = trace.dump()[0]
    assert event["event"] == "verify.result" and "latency_ms" in event
