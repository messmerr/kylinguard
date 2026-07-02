import pytest
from pydantic import ValidationError

from kylinguard.models import (
    PlannerOutput, PlanStep, RiskLevel, max_risk,
)


def test_风险等级取最严():
    assert max_risk(RiskLevel.LOW, RiskLevel.HIGH, RiskLevel.MEDIUM) == RiskLevel.HIGH
    assert max_risk(RiskLevel.LOW) == RiskLevel.LOW


def test_规划输出解析():
    data = {
        "thought": "先看负载",
        "steps": [{"tool": "sysinfo.top_processes",
                   "arguments": {"limit": 5},
                   "purpose": "查看 CPU 占用最高的进程",
                   "risk": "low"}],
        "final_answer": None,
    }
    out = PlannerOutput.model_validate(data)
    assert out.steps[0].risk == RiskLevel.LOW
    assert out.final_answer is None


def test_非法风险等级拒绝():
    with pytest.raises(ValidationError):
        PlanStep(tool="a.b", arguments={}, purpose="x", risk="危险")
