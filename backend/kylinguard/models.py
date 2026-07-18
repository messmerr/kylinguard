"""全项目共享数据模型。"""
from enum import Enum

from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    LOW = "low"        # 只读，自动放行
    MEDIUM = "medium"  # 改动可逆，需一键确认
    HIGH = "high"      # 删除/改配置/停服务，需二次确认


class PermissionMode(str, Enum):
    """全局审批模式。

    模式只决定 KylinGuard 是否自动批准一类操作，不会提升运行服务的
    Linux/Windows 账户权限；即使 ``full_access`` 也仍受操作系统约束。
    """

    READ_ONLY = "read_only"
    ASK = "ask"
    AUTO_REVIEW = "auto_review"
    FULL_ACCESS = "full_access"


class PermissionGrantScope(str, Enum):
    ONCE = "once"
    SESSION = "session"


class PermissionDecision(str, Enum):
    DENY = "deny"
    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"
    AUTHORIZE_PATH = "authorize_path"


class PermissionContext(BaseModel):
    # 审批模式与自动执行范围是全局设置；流水线读取时会附上当前会话 ID，
    # 全局设置接口返回空字符串。
    session_id: str = ""
    mode: PermissionMode = PermissionMode.ASK
    auto_review_roots: list[str] = Field(default_factory=list)
    # 完全访问入口默认隐藏；只有管理员在“权限与安全”中单独揭示后，
    # 全局权限接口才接受切换到 full_access。
    full_access_visible: bool = False
    version: int = Field(default=1, ge=1)
    updated_at: float
    updated_by: str = ""
    execution_profile: str = ""


class PermissionGrant(BaseModel):
    id: str
    session_id: str
    scope: PermissionGrantScope
    action_fingerprint: str
    capability: str
    resource: str = ""
    context_version: int = Field(ge=1)
    granted_by: str
    created_at: float
    expires_at: float | None = None
    expiry_observed_at: float | None = None
    consumed_at: float | None = None
    revoked_at: float | None = None


class PermissionRequest(BaseModel):
    id: str
    session_id: str
    action_fingerprint: str
    context_version: int = Field(ge=1)
    capability: str
    resource: str = ""
    suggested_path: str = ""
    single_action_only: bool = False
    created_at: float


class PermissionResolution(BaseModel):
    request_id: str
    decision: PermissionDecision
    operator: str
    context_version: int = Field(ge=1)
    grant_id: str | None = None
    authorized_path: str | None = None


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
    custom: bool = False
    # registry=内置声明；administrator=管理员按定义摘要设置；
    # platform_default=第三方工具尚未分级，按最高风险收敛。
    risk_source: str = "registry"


class PlanStep(BaseModel):
    tool: str  # 限定名 "server.tool_name"
    arguments: dict = Field(default_factory=dict)
    purpose: str
    risk: RiskLevel  # 规划模型自评风险


class PlannerOutput(BaseModel):
    thought: str = ""
    steps: list[PlanStep] = Field(default_factory=list)
    final_answer: str | None = None
    # 只在独立的 Skill 路由阶段消费；普通规划阶段出现时按协议错误处理。
    selected_skill_id: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$",
    )


class RuleDecision(str, Enum):
    DENY = "deny"      # hard=True 永久拒绝；hard=False 表示必须显式授权
    ALLOW = "allow"    # 命中只读白名单，规则层放行
    REVIEW = "review"  # 规则层不表态，交后续闸门


class RuleVerdict(BaseModel):
    decision: RuleDecision
    reason: str
    matched_rule: str | None = None
    # DENY 分为不可越过的安全红线和“默认不放行、可由显式权限模式处理”的
    # 策略拒绝。默认 True 保持第三方/旧代码构造 DENY 时的 fail-closed 语义。
    hard: bool = True


class ReviewVerdict(BaseModel):
    safe: bool
    matches_intent: bool
    risk: RiskLevel
    reason: str
    available: bool = True


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
