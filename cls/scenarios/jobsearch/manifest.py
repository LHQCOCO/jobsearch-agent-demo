"""求职场景装配：J1–J7 + analyst/tailor/auditor + 规划器。"""
from __future__ import annotations

from ..base import Scenario
from .planner import JobPlanner, build_task
from .roles import ANALYST_ROLE, AUDITOR_ROLE, TAILOR_ROLE, JobRoleRunner
from .tools import TOOL_SPECS


def render(ctx) -> dict:
    results = ctx.state.get("results", {})
    jd = results.get("s1") or {}
    analyst = results.get("s4") or {}
    review = results.get("s5") or {}
    brief = results.get("s7") or {}
    application = results.get("s6") or {}
    draft = review.get("draft") or {}
    return {
        "jd_id": jd.get("jd_id", ""),
        "title": jd.get("title", ""),
        "company": jd.get("company", ""),
        "match_counts": analyst.get("summary", {}),
        "gaps": analyst.get("gaps", []),
        "bullets": draft.get("bullets", []),
        "audit_ok": bool(review.get("ok")),
        "audit_rounds": review.get("rounds", 0),
        "rejections": list(ctx.state.get("jobsearch_rejections", [])),
        "application_id": application.get("application_id", ""),
        "brief_path": brief.get("path", ""),
    }


def build_scenario(adapter=None) -> Scenario:
    return Scenario(
        name="jobsearch",
        title="求职决策",
        version="1",
        tools=tuple(TOOL_SPECS),
        roles={"analyst": ANALYST_ROLE, "tailor": TAILOR_ROLE, "auditor": AUDITOR_ROLE},
        planner=JobPlanner(adapter=adapter),
        task_builder=build_task,
        renderer=render,
        role_runner_factory=lambda registry, runtime_adapter=None:
            JobRoleRunner(registry, adapter or runtime_adapter),
    )


JOBSEARCH_SCENARIO = build_scenario()
