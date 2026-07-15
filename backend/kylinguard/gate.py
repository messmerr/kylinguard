"""风险分级门控（三道闸第三道）：综合三方判定给出最终动作。

原则：确定性硬规则拒绝不可被更低层推翻；独立 Reviewer 负责提供风险告警，
不代替管理员作最终授权决定。风险取各方最严；低危自动放行、中危一键确认、
高危或 Reviewer 告警二次确认。run_command 命中只读白名单时降为低危。

与执行器的关系：门控决定"何时问人"（审批时机），执行器提供完整能力并
受实际 OS 身份约束——授权策略与技术能力是两个正交维度。
"""
from kylinguard.models import (
    GateAction, GateDecision, ReviewVerdict, RiskLevel,
    RuleDecision, RuleVerdict, ToolMeta, max_risk,
)

_ACTION_BY_RISK = {
    RiskLevel.LOW: GateAction.AUTO,
    RiskLevel.MEDIUM: GateAction.CONFIRM,
    RiskLevel.HIGH: GateAction.DOUBLE_CONFIRM,
}

_RISK_SOURCE_LABELS = {
    "registry": "内置策略",
    "administrator": "管理员设置",
    "platform_default": "平台默认",
}


def decide(meta: ToolMeta, rule: RuleVerdict, review: ReviewVerdict,
           planner_risk: RiskLevel) -> GateDecision:
    if rule.decision == RuleDecision.DENY and rule.hard:
        return GateDecision(action=GateAction.DENY, risk=RiskLevel.HIGH,
                            reason=f"规则引擎拒绝：{rule.reason}")

    if meta.dynamic and rule.decision == RuleDecision.ALLOW:
        risk = max_risk(RiskLevel.LOW, review.risk)
        reason = "命中只读白名单，静态规则判定为只读"
    else:
        risk = max_risk(meta.risk, review.risk, planner_risk)
        prefix = ("静态规则要求显式权限；" if
                  rule.decision == RuleDecision.DENY else "")
        source = _RISK_SOURCE_LABELS.get(meta.risk_source, meta.risk_source)
        reason = (f"{prefix}综合风险 {risk.value}（工具基线 {meta.risk.value}"
                  f"·{source} / "
                  f"审查员 {review.risk.value} / 规划自评 {planner_risk.value}）")

    concerns = []
    if not review.safe:
        concerns.append("安全性存疑")
    if not review.matches_intent:
        concerns.append("可能偏离管理员原始意图")
    if concerns:
        risk = RiskLevel.HIGH
        reason = (
            f"独立审查员高风险告警（{'、'.join(concerns)}）：{review.reason}；"
            f"{reason}"
        )

    return GateDecision(action=_ACTION_BY_RISK[risk], risk=risk, reason=reason)
