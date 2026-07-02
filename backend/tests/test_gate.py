from kylinguard.gate import decide
from kylinguard.models import (
    GateAction, ReviewVerdict, RiskLevel, RuleDecision, RuleVerdict, ToolMeta,
)


def _meta(risk=RiskLevel.LOW, dynamic=False):
    return ToolMeta(server="s", tool="t", risk=risk, dynamic=dynamic)


def _rule(decision=RuleDecision.REVIEW):
    return RuleVerdict(decision=decision, reason="r")


def _review(safe=True, intent=True, risk=RiskLevel.LOW):
    return ReviewVerdict(safe=safe, matches_intent=intent, risk=risk, reason="r")


def test_规则拒绝一票否决():
    d = decide(_meta(), _rule(RuleDecision.DENY), _review(), RiskLevel.LOW)
    assert d.action == GateAction.DENY


def test_审查员不安全一票否决():
    d = decide(_meta(), _rule(), _review(safe=False), RiskLevel.LOW)
    assert d.action == GateAction.DENY


def test_审查员判不符意图一票否决():
    d = decide(_meta(), _rule(), _review(intent=False), RiskLevel.LOW)
    assert d.action == GateAction.DENY


def test_只读自动放行():
    d = decide(_meta(RiskLevel.LOW), _rule(), _review(), RiskLevel.LOW)
    assert d.action == GateAction.AUTO


def test_任何一方喊高危就按高危():
    d = decide(_meta(RiskLevel.LOW), _rule(),
               _review(risk=RiskLevel.HIGH), RiskLevel.LOW)
    assert d.action == GateAction.DOUBLE_CONFIRM
    d = decide(_meta(RiskLevel.LOW), _rule(), _review(), RiskLevel.MEDIUM)
    assert d.action == GateAction.CONFIRM


def test_run_command白名单命中降为自动():
    d = decide(_meta(RiskLevel.MEDIUM, dynamic=True),
               _rule(RuleDecision.ALLOW), _review(), RiskLevel.LOW)
    assert d.action == GateAction.AUTO


def test_run_command未命中白名单至少确认():
    d = decide(_meta(RiskLevel.MEDIUM, dynamic=True),
               _rule(), _review(), RiskLevel.LOW)
    assert d.action == GateAction.CONFIRM


def test_未注册工具高危需二次确认():
    d = decide(_meta(RiskLevel.HIGH), _rule(), _review(), RiskLevel.LOW)
    assert d.action == GateAction.DOUBLE_CONFIRM
