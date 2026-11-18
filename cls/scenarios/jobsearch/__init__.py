"""求职决策场景插件：复用 cls/agent 的 Executor、ReviewLoop、EvidenceChecker。"""
from .manifest import JOBSEARCH_SCENARIO, build_scenario, render

__all__ = ["JOBSEARCH_SCENARIO", "build_scenario", "render"]
