import time

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


def test_完全访问自动执行但不能写控制面数据库(tmp_path):
    settings = Settings(_env_file=None, db_path=str(tmp_path / "control.db"))
    normal = _file_action(tmp_path, path=str(tmp_path / "note.md"), settings=settings)
    context = _context(PermissionMode.FULL_ACCESS)
    assert apply_permission_mode(context, normal, _base()).action == GateAction.AUTO

    control = _file_action(
        tmp_path, path=str(tmp_path / "control.db"), settings=settings)
    decision = apply_permission_mode(context, control, _base())
    assert decision.action == GateAction.DENY
    assert "控制面" in decision.reason


def test_命令与批处理中的控制面路径都是硬拒绝(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=None, db_path="data/kylinguard.db")
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
    assert "控制面" in single.hard_block_reason
    assert apply_permission_mode(
        _context(PermissionMode.FULL_ACCESS), single, _base()
    ).action == GateAction.DENY

    batch = describe_action(PlanStep(
        tool="run_command.run_batch",
        arguments={"commands": [["rm", "data/kylinguard.db"]]},
        purpose="误删", risk=RiskLevel.HIGH,
    ), meta.model_copy(update={"tool": "run_batch"}), rule, settings)
    assert "控制面" in batch.hard_block_reason


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
