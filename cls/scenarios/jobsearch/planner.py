"""求职场景规划器。

内核里的 LLMPlanner 还是空壳（抛 NotImplementedError），为了不改内核，
这里实现一个同协议的场景规划器：
- 没有模型 → 固定五步 + 可选的记录/渲染；
- 有模型 → 只让模型在固定步骤里挑选子集，参数与依赖仍由模板绑定；
- 模型不可用、输出不合法、超过预算 → 一次重试后回退固定计划。
"""
from __future__ import annotations

import json

from ...agent.adapter import ModelUnavailable
from ...agent.types import MODEL_UNAVAILABLE, Plan, Step
from .prompts import PLANNER_SYSTEM

_REQUIRED = ("s1", "s2", "s3")

_STEP_SCHEMA = {
    "type": "object",
    "required": ["steps"],
    "properties": {"steps": {"type": "array", "items": {"type": "string"}}},
    "additionalProperties": False,
}


def build_task(inputs: dict) -> dict:
    inputs = inputs or {}
    return {
        "jd_text": inputs.get("jd_text", ""),
        "resume_text": inputs.get("resume_text", ""),
        "resume_path": inputs.get("resume_path", ""),
        "source_url": inputs.get("source_url", ""),
        "save": bool(inputs.get("save", False)),
        "run_id": inputs.get("run_id", ""),
    }


def build_steps(task: dict) -> list[Step]:
    task = task or {}
    steps = [
        Step("s1", "", tool="parse_jd",
             args={"raw_text": "{task.jd_text}", "source_url": "{task.source_url}"}),
        Step("s2", "", tool="load_resume",
             args={"text": "{task.resume_text}", "path": "{task.resume_path}"}),
        Step("s3", "", tool="match_requirements",
             args={"jd_id": "{s1.jd_id}", "resume_id": "{s2.resume_id}"},
             depends_on=("s1", "s2")),
        Step("s4", "analyst", args={}, depends_on=("s3",)),
        Step("s5", "review", args={}, depends_on=("s4",)),
    ]
    if task.get("save"):
        steps.append(Step("s6", "", tool="save_application",
                          args={"action": "save", "jd_id": "{s1.jd_id}"},
                          depends_on=("s2",)))
        steps.append(Step("s7", "", tool="render_brief",
                          args={"run_id": "{task.run_id}"}, depends_on=("s6",)))
    else:
        steps.append(Step("s7", "", tool="render_brief",
                          args={"run_id": "{task.run_id}"}, depends_on=("s5",)))
    return steps


def _catalog(steps: list[Step]) -> str:
    names = {
        "s1": "解析 JD", "s2": "加载简历", "s3": "逐条匹配", "s4": "分析师汇总",
        "s5": "改写与审核循环", "s6": "记录投递", "s7": "生成投递卡",
    }
    lines = [f"- {s.id}（{s.tool or s.role}）：{names.get(s.id, '')}" for s in steps]
    return "可选步骤：\n" + "\n".join(lines) + "\n请只返回要执行的步骤 id 数组。"


class JobPlanner:
    def __init__(self, adapter=None):
        self.adapter = adapter

    def plan(self, task: dict, tools, budget) -> Plan:
        steps = build_steps(task)
        if self.adapter is None:
            return Plan(steps, reason="template")

        last_error = ""
        for _ in range(2):
            try:
                reply = self.adapter.complete(
                    [{"role": "system", "content": PLANNER_SYSTEM},
                     {"role": "user", "content": _catalog(steps)}],
                    schema=_STEP_SCHEMA, max_tokens=120, temperature=0.0,
                )
                data = json.loads(reply.text)
                chosen = data.get("steps")
                if not isinstance(chosen, list):
                    raise ValueError("steps 不是数组")
                ids = [str(x) for x in chosen]
                if not set(_REQUIRED) <= set(ids):
                    raise ValueError("缺少必需步骤")
                by_id = {s.id: s for s in steps}
                selected = [by_id[i] for i in ids if i in by_id]
                if not selected:
                    raise ValueError("没有可执行步骤")
                keep = {s.id for s in selected}
                selected = [s for s in selected
                            if all(dep in keep for dep in s.depends_on)]
                order = {s.id: i for i, s in enumerate(steps)}
                selected.sort(key=lambda s: order[s.id])
                if len(selected) > int(getattr(budget, "max_steps", 12)):
                    raise ValueError("步骤数超过预算")
                if not set(_REQUIRED) <= {s.id for s in selected}:
                    raise ValueError("依赖裁剪后缺少必需步骤")
                return Plan(selected, reason="llm")
            except ModelUnavailable as exc:
                last_error = f"{MODEL_UNAVAILABLE}:{exc}"
                break
            except Exception as exc:  # noqa: BLE001 - 任何解析失败都退化为固定计划
                last_error = f"{exc.__class__.__name__}:{exc}"
                continue

        reason = "fallback:MODEL_UNAVAILABLE" if last_error.startswith(MODEL_UNAVAILABLE) \
            else "fallback:PLAN_INVALID"
        return Plan(steps, reason=reason)

    def replan(self, plan: Plan, observation: dict) -> Plan:
        return plan
