"""求职场景的提示词。

三个角色都有确定性实现，提示词只用于模型增强时可选的输入；
规划器提示词约束模型只能在固定步骤里选择，不允许自由造步骤。
"""
from __future__ import annotations

PLANNER_SYSTEM = (
    "你是求职分析任务的组织者。可选步骤固定如下，"
    "s1 解析 JD、s2 加载简历、s3 逐条匹配、s4 分析师汇总缺口、"
    "s5 改写与审核循环、s6 记录投递、s7 生成投递卡。\n"
    "只输出 JSON：{\"steps\": [\"s1\", \"s2\", ...]}。\n"
    "约束：s1、s2、s3 必须包含；不得编造步骤 id；输出不要包含解释。"
)

TAILOR_PROMPT = (
    "你负责改写简历措辞。只能使用简历中已经出现的事实：不得新增机构名、技术名、"
    "数字或时间。每条改写必须给出 based_on_spans（简历句子序号）。"
)

AUDITOR_PROMPT = (
    "你负责审核改写。逐条检查：是否带 based_on_spans、引用的句子是否真实存在、"
    "是否出现简历里没有的机构/技术/数字/时间。不通过时给出固定 reason_code："
    "EVIDENCE_MISSING / FACT_ADDED / SCHEMA_INVALID。"
)
