"""风险分级门控（三道闸第三道）：综合三方判定给出最终动作。

原则（对齐 Codex execpolicy"取最严格裁决"）：拒绝一票否决且不可被
更低层推翻；风险取各方最严；低危自动放行、中危一键确认、高危二次确认。
run_command 命中只读白名单时降为低危。

与受限执行器的关系：门控决定"何时问人"（审批时机），执行器决定
"技术上能做什么"（能力边界）——两个正交维度，互为纵深防御。
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


def decide(meta: ToolMeta, rule: RuleVerdict, review: ReviewVerdict,
           planner_risk: RiskLevel) -> GateDecision:
    if rule.decision == RuleDecision.DENY and rule.hard:
        return GateDecision(action=GateAction.DENY, risk=RiskLevel.HIGH,
                            reason=f"规则引擎拒绝：{rule.reason}")

    if not review.safe or not review.matches_intent:
        return GateDecision(action=GateAction.DENY, risk=RiskLevel.HIGH,
                            reason=f"LLM 审查员拒绝：{review.reason}")

    if meta.dynamic and rule.decision == RuleDecision.ALLOW:
        risk = max_risk(RiskLevel.LOW, review.risk)
        reason = "命中只读白名单，审查员无异议"
    else:
        risk = max_risk(meta.risk, review.risk, planner_risk)
        prefix = ("静态规则要求显式权限；" if
                  rule.decision == RuleDecision.DENY else "")
        reason = (f"{prefix}综合风险 {risk.value}（工具声明 {meta.risk.value} / "
                  f"审查员 {review.risk.value} / 规划自评 {planner_risk.value}）")

    return GateDecision(action=_ACTION_BY_RISK[risk], risk=risk, reason=reason)
