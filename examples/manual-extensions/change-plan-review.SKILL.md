---
id: change-plan-review
name: 复杂变更方案评审
description: 当用户要规划数据库迁移、系统升级或其他包含约束与备选路线的复杂变更时使用；通过结构化推演检查假设、风险和回滚方案。
version: "1.0.0"
required_tools:
  - sequential-thinking-demo.sequential_thinking
---
# 目标

把复杂变更拆成可验证的步骤，在给出结论前检查关键假设、停机约束和回滚条件。

# 工作流

1. 调用 `sequential-thinking-demo.sequential_thinking` 梳理目标、约束和未知项。
2. 至少比较一条主方案和一条备选方案；发现假设不成立时修订前面的判断。
3. 将高风险步骤与可逆步骤分开，明确每个阶段的验证点和回滚触发条件。
4. 只输出方案，不执行真实系统变更。

# 输出要求

按“前提、执行阶段、验证点、风险、回滚方案”组织最终答案，不展示无关的中间推演文本。
