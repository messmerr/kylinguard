import time

import pytest

from kylinguard.authorization import (
    apply_permission_mode,
    describe_action,
    trusted_workspace_allows,
)
from kylinguard.config import Settings
from kylinguard.models import (
    GateAction,
    GateDecision,
    PermissionGrantScope,
    PermissionMode,
    PlanStep,
    RiskLevel,
    RuleDecision,
    RuleVerdict,
    SessionPermissionContext,
    ToolMeta,
)


def _context(mode, roots=None, *, expired=False):
    return SessionPermissionContext(
        session_id="s", mode=mode, trusted_roots=roots or [],
        expires_at=time.time() + 300, version=1, updated_at=time.time(),
        expired=expired,
    )


def _base(action=GateAction.CONFIRM, risk=RiskLevel.MEDIUM):
    return GateDecision(action=action, risk=risk, reason="risk")


def _file_action(tmp_path, tool="write_file", path=None, settings=None):
    path = path or str(tmp_path / "note.md")
    step = PlanStep(
        tool=f"files.{tool}", arguments={"path": path, "content": "x"},
        purpose="记录", risk=RiskLevel.MEDIUM,
    )
    meta = ToolMeta(server="files", tool=tool, risk=RiskLevel.MEDIUM)
    return describe_action(
        step, meta, RuleVerdict(decision=RuleDecision.REVIEW, reason="r"),
        settings or Settings(_env_file=None, db_path=str(tmp_path / "control.db")),
    )


def test_只读模式拒绝普通文件写入(tmp_path):
    action = _file_action(tmp_path)
    decision = apply_permission_mode(
        _context(PermissionMode.READ_ONLY), action, _base())
    assert decision.action == GateAction.DENY


def test_可信目录自动允许创建和修改但不允许删除(tmp_path):
    root = tmp_path / "docs"
    action = _file_action(tmp_path, path=str(root / "note.md"))
    context = _context(PermissionMode.TRUSTED_WORKSPACE, [str(root)])
    assert trusted_workspace_allows(context, action)
    assert apply_permission_mode(context, action, _base()).action == GateAction.AUTO

    delete = _file_action(tmp_path, tool="delete", path=str(root / "note.md"))
    assert not trusted_workspace_allows(context, delete)
    assert apply_permission_mode(context, delete, _base(
        GateAction.DOUBLE_CONFIRM, RiskLevel.HIGH)).action == GateAction.DOUBLE_CONFIRM


def test_过期可信目录回退到逐项确认(tmp_path):
    action = _file_action(tmp_path)
    context = _context(PermissionMode.TRUSTED_WORKSPACE, [str(tmp_path)], expired=True)
    assert apply_permission_mode(context, action, _base()).action == GateAction.CONFIRM


def test_完全访问覆盖产品路径限制但仍受OS身份约束(tmp_path):
    settings = Settings(_env_file=None, db_path=str(tmp_path / "control.db"))
    normal = _file_action(tmp_path, path=str(tmp_path / "note.md"), settings=settings)
    context = _context(PermissionMode.FULL_ACCESS)
    assert apply_permission_mode(context, normal, _base()).action == GateAction.AUTO

    control = _file_action(
        tmp_path, path=str(tmp_path / "control.db"), settings=settings)
    decision = apply_permission_mode(context, control, _base())
    assert control.hard_block_reason
    assert decision.action == GateAction.AUTO
    assert "产品层不再限制" in decision.reason


def test_完全访问覆盖reviewer告警产生的二次确认(tmp_path):
    context = _context(PermissionMode.FULL_ACCESS)
    mutable = _file_action(tmp_path)
    readonly = _file_action(tmp_path, tool="read_file")
    warning = _base(GateAction.DOUBLE_CONFIRM, RiskLevel.HIGH)

    mutable_decision = apply_permission_mode(context, mutable, warning)
    readonly_decision = apply_permission_mode(context, readonly, warning)

    assert mutable_decision.action == GateAction.AUTO
    assert mutable_decision.risk == RiskLevel.HIGH
    assert readonly_decision.action == GateAction.AUTO
    assert readonly_decision.risk == RiskLevel.HIGH


def test_完全访问仍不覆盖硬规则拒绝(tmp_path):
    action = _file_action(tmp_path)
    hard_denial = _base(GateAction.DENY, RiskLevel.HIGH)
    decision = apply_permission_mode(
        _context(PermissionMode.FULL_ACCESS), action, hard_denial)
    assert decision.action == GateAction.DENY


def test_只读模式在reviewer告警下仍拒绝修改(tmp_path):
    action = _file_action(tmp_path)
    warning = _base(GateAction.DOUBLE_CONFIRM, RiskLevel.HIGH)
    decision = apply_permission_mode(
        _context(PermissionMode.READ_ONLY), action, warning)
    assert decision.action == GateAction.DENY
    assert decision.risk == RiskLevel.HIGH


def test_命令中的控制面路径是高风险信号而非伪隔离(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        _env_file=None,
        db_path="data/kylinguard.db",
        workspace_root=str(tmp_path),
    )
    meta = ToolMeta(
        server="run_command", tool="run_command",
        risk=RiskLevel.MEDIUM, dynamic=True,
    )
    rule = RuleVerdict(
        decision=RuleDecision.DENY, reason="写命令", hard=False,
    )
    single = describe_action(PlanStep(
        tool="run_command.run_command",
        arguments={"command": "rm data/kylinguard.db"},
        purpose="误删", risk=RiskLevel.HIGH,
    ), meta, rule, settings)
    assert single.control_path_signal is True
    assert single.hard_block_reason == ""
    assert apply_permission_mode(
        _context(PermissionMode.FULL_ACCESS), single, _base()
    ).action == GateAction.AUTO

    batch = describe_action(PlanStep(
        tool="run_command.run_batch",
        arguments={"commands": [["rm", "data/kylinguard.db"]]},
        purpose="误删", risk=RiskLevel.HIGH,
    ), meta.model_copy(update={"tool": "run_batch"}), rule, settings)
    assert batch.control_path_signal is True
    assert batch.hard_block_reason == ""


def test_命令相对路径按调用cwd而非后端进程目录解析(tmp_path):
    workspace = tmp_path / "workspace"
    control = workspace / "state" / "control.db"
    settings = Settings(
        _env_file=None,
        db_path=str(control),
        workspace_root=str(tmp_path / "other"),
    )
    meta = ToolMeta(
        server="run_command", tool="run_command",
        risk=RiskLevel.MEDIUM, dynamic=True,
    )
    rule = RuleVerdict(
        decision=RuleDecision.DENY, reason="写命令", hard=False,
    )
    action = describe_action(PlanStep(
        tool="run_command.run_command",
        arguments={"command": "rm state/control.db", "cwd": str(workspace)},
        purpose="误删", risk=RiskLevel.HIGH,
    ), meta, rule, settings)

    assert action.control_path_signal is True
    assert action.hard_block_reason == ""


@pytest.mark.parametrize("command", [
    "cat /proc/meminfo",
    "ls /sys/class/net",
    "ls -l /dev/null",
    "cat /etc/sudoers",
])
def test_系统路径由风险与OS权限管理而非冒充KylinGuard控制面(
    tmp_path, command,
):
    settings = Settings(_env_file=None, db_path=str(tmp_path / "control.db"))
    meta = ToolMeta(
        server="run_command", tool="run_command",
        risk=RiskLevel.MEDIUM, dynamic=True,
    )
    action = describe_action(PlanStep(
        tool="run_command.run_command",
        arguments={"command": command},
        purpose="系统运维", risk=RiskLevel.HIGH,
    ), meta, RuleVerdict(
        decision=RuleDecision.REVIEW, reason="需复核", hard=False,
    ), settings)

    assert action.hard_block_reason == ""


def test_递归删除控制面祖先目录也被拒绝(tmp_path):
    settings = Settings(
        _env_file=None, db_path=str(tmp_path / "state" / "control.db"))
    action = _file_action(
        tmp_path, tool="delete", path=str(tmp_path / "state"),
        settings=settings,
    )
    assert "控制面" in action.hard_block_reason


def test_服务能力按具体动作区分(tmp_path):
    settings = Settings(_env_file=None, db_path=str(tmp_path / "control.db"))
    meta = ToolMeta(server="services", tool="restart_service",
                    risk=RiskLevel.MEDIUM)
    step = PlanStep(tool="services.restart_service",
                    arguments={"name": "nginx"}, purpose="重启",
                    risk=RiskLevel.MEDIUM)
    action = describe_action(
        step, meta, RuleVerdict(decision=RuleDecision.REVIEW, reason="r"),
        settings,
    )
    assert action.capability == "service.restart"


@pytest.mark.parametrize("matched_rule", [
    "dangerous_command",
    "protected_path",
    "privilege_escalator",
    "control_command",
])
def test_命令高危规则标记为破坏性(tmp_path, matched_rule):
    settings = Settings(_env_file=None, db_path=str(tmp_path / "control.db"))
    step = PlanStep(
        tool="run_command.run_command",
        arguments={"command": "custom-tool --apply"},
        purpose="执行操作",
        risk=RiskLevel.MEDIUM,
    )
    meta = ToolMeta(
        server="run_command", tool="run_command",
        risk=RiskLevel.MEDIUM, dynamic=True,
    )
    rule = RuleVerdict(
        decision=RuleDecision.DENY,
        reason="需要显式权限",
        matched_rule=matched_rule,
        hard=False,
    )

    action = describe_action(step, meta, rule, settings)
    assert action.destructive is True


def test_payload_executor不再一概标记为破坏性(tmp_path):
    settings = Settings(_env_file=None, db_path=str(tmp_path / "control.db"))
    step = PlanStep(
        tool="run_command.run_command",
        arguments={"command": "python3 -c print(1)"},
        purpose="计算结果",
        risk=RiskLevel.MEDIUM,
    )
    meta = ToolMeta(
        server="run_command", tool="run_command",
        risk=RiskLevel.MEDIUM, dynamic=True,
    )
    rule = RuleVerdict(
        decision=RuleDecision.DENY,
        reason="载荷执行器需要显式权限",
        matched_rule="payload_executor",
        hard=False,
    )

    action = describe_action(step, meta, rule, settings)
    assert action.mutable is True
    assert action.destructive is False


def test_自定义保护路径覆盖结构化文件工具并提升确认强度(tmp_path):
    protected = tmp_path / "managed"
    step = PlanStep(
        tool="files.write_file",
        arguments={"path": str(protected / "config.ini"), "content": "x"},
        purpose="修改配置", risk=RiskLevel.MEDIUM,
    )
    meta = ToolMeta(
        server="files", tool="write_file", risk=RiskLevel.MEDIUM,
    )
    action = describe_action(
        step,
        meta,
        RuleVerdict(decision=RuleDecision.REVIEW, reason="r"),
        Settings(_env_file=None, db_path=str(tmp_path / "control.db")),
        protected_prefixes=(str(protected),),
    )

    assert action.policy_protected is True
    assert action.destructive is True
    assert apply_permission_mode(
        _context(PermissionMode.ASK), action, _base(),
    ).action == GateAction.DOUBLE_CONFIRM
    assert apply_permission_mode(
        _context(PermissionMode.FULL_ACCESS), action, _base(),
    ).action == GateAction.AUTO


def test_一次或会话授权只覆盖已绑定动作(tmp_path):
    action = _file_action(tmp_path)
    decision = apply_permission_mode(
        _context(PermissionMode.ASK), action, _base(), has_grant=True)
    assert decision.action == GateAction.AUTO


def test_破坏性操作不能被会话范围授权自动放行(tmp_path):
    action = _file_action(tmp_path, tool="delete")
    session_decision = apply_permission_mode(
        _context(PermissionMode.ASK), action,
        _base(GateAction.DOUBLE_CONFIRM, RiskLevel.HIGH),
        has_grant=True, grant_scope=PermissionGrantScope.SESSION,
    )
    once_decision = apply_permission_mode(
        _context(PermissionMode.ASK), action,
        _base(GateAction.DOUBLE_CONFIRM, RiskLevel.HIGH),
        has_grant=True, grant_scope=PermissionGrantScope.ONCE,
    )
    assert session_decision.action == GateAction.DOUBLE_CONFIRM
    assert once_decision.action == GateAction.AUTO
