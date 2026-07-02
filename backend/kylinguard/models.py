"""全项目共享数据模型。"""
from enum import Enum

from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    LOW = "low"        # 只读，自动放行
    MEDIUM = "medium"  # 改动可逆，需一键确认
    HIGH = "high"      # 删除/改配置/停服务，需二次确认


_RISK_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}


def max_risk(*levels: RiskLevel) -> RiskLevel:
    return max(levels, key=lambda lv: _RISK_ORDER[lv])


class ToolMeta(BaseModel):
    """工具注册元数据：校验器直接消费；未注册工具默认按最高危处理。"""
    server: str
    tool: str
    risk: RiskLevel
    needs_sudo: bool = False
    dynamic: bool = False  # True = run_command 类，风险随命令内容动态判定
    description: str = ""


class PlanStep(BaseModel):
    tool: str  # 限定名 "server.tool_name"
    arguments: dict = Field(default_factory=dict)
    purpose: str
    risk: RiskLevel  # 规划模型自评风险


class PlannerOutput(BaseModel):
    thought: str = ""
    steps: list[PlanStep] = Field(default_factory=list)
    final_answer: str | None = None


class RuleDecision(str, Enum):
    DENY = "deny"      # 命中黑名单/保护路径/元字符，直接拒绝
    ALLOW = "allow"    # 命中只读白名单，规则层放行
    REVIEW = "review"  # 规则层不表态，交后续闸门


class RuleVerdict(BaseModel):
    decision: RuleDecision
    reason: str
    matched_rule: str | None = None


class ReviewVerdict(BaseModel):
    safe: bool
    matches_intent: bool
    risk: RiskLevel
    reason: str


class GateAction(str, Enum):
    AUTO = "auto"
    CONFIRM = "confirm"
    DOUBLE_CONFIRM = "double_confirm"
    DENY = "deny"


class GateDecision(BaseModel):
    action: GateAction
    risk: RiskLevel
    reason: str


class ExecResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool = False
    timed_out: bool = False
