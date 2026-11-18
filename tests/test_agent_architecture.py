"""架构约束：内核不得依赖场景层或授权模块，注册保持显式。"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT / "cls" / "agent"
SCENARIOS_DIR = ROOT / "cls" / "scenarios"


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = "." * (node.level or 0) + (node.module or "")
            mods.append(base)
            mods.extend(f"{base}.{alias.name}" for alias in node.names)
    return mods


def _parts(module: str) -> set[str]:
    return {p for p in module.replace("..", ".").split(".") if p}


def test_agent_core_does_not_import_scenarios_or_license():
    offenders = []
    for path in sorted(AGENT_DIR.glob("*.py")):
        for module in _imports(path):
            parts = _parts(module)
            if "scenarios" in parts or "license" in parts:
                offenders.append(f"{path.name} -> {module}")
    assert not offenders, "内核出现反向依赖：" + "; ".join(offenders)


def test_agent_core_imports_only_stdlib_and_cls_base_modules():
    """内核允许依赖 cls 的基础模块（config/kb 等由场景注入），但不得依赖授权与外部框架。"""
    forbidden = {"langchain", "langgraph", "llamaindex", "openai", "requests"}
    for path in sorted(AGENT_DIR.glob("*.py")):
        for module in _imports(path):
            head = module.lstrip(".").split(".")[0]
            assert head not in forbidden, f"{path.name} 引入了 {module}"


def test_scenario_registry_is_explicit():
    init = (SCENARIOS_DIR / "__init__.py").read_text(encoding="utf-8")
    assert "importlib" not in init
    assert "from .jobsearch import" in init


def test_jobsearch_scenario_declares_tools_and_roles():
    from cls.scenarios import JOBSEARCH_SCENARIO, SCENARIOS

    assert SCENARIOS["jobsearch"] is JOBSEARCH_SCENARIO
    assert len(JOBSEARCH_SCENARIO.tools) == 7
    assert set(JOBSEARCH_SCENARIO.roles) == {"analyst", "tailor", "auditor"}
    for spec, fn in JOBSEARCH_SCENARIO.tools:
        assert callable(fn)
        assert spec.side_effect in ("none", "local_write", "network")
    names = {spec.name for spec, _ in JOBSEARCH_SCENARIO.tools}
    assert names == {"parse_jd", "load_resume", "search_resume", "match_requirements",
                     "detect_new_facts", "save_application", "render_brief"}


def test_scenario_contract_is_reusable():
    """Scenario 契约只要求数据与回调，第二个场景（求职分析）可直接复用。"""
    from cls.agent.planner import ChecklistPlanner
    from cls.agent.types import Step
    from cls.scenarios.base import Scenario

    stub = Scenario(
        name="job", title="求职分析",
        tools=(),
        roles={},
        planner=ChecklistPlanner(lambda task: [Step("s1", "reviewer")]),
        task_builder=lambda inputs: {"doc_id": inputs.get("doc_id", "")},
        renderer=lambda ctx: {"ok": True},
    )
    assert stub.build_task({"doc_id": "d1"})["doc_id"] == "d1"
    assert stub.render(None) == {"ok": True}
