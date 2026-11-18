"""场景注册入口。

展示版只带求职场景；合同场景已剥离。
显式 import，保证静态分析能收集到模块；禁止动态扫描注册。
"""
from .base import Scenario
from .jobsearch import manifest as jobsearch_manifest
from .jobsearch.manifest import JOBSEARCH_SCENARIO

SCENARIOS = {
    "jobsearch": JOBSEARCH_SCENARIO,
}

__all__ = ["Scenario", "SCENARIOS", "JOBSEARCH_SCENARIO", "jobsearch_manifest"]
