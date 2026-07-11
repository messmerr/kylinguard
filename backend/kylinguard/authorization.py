"""把工具调用转换为稳定能力描述，并应用会话权限模式。"""

from __future__ import annotations

import os
import pwd
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

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
from kylinguard.sanitization import canonical_fingerprint, redact_text


_FILE_READ_TOOLS = {"read_file", "list_directory"}
_FILE_WRITE_TOOLS = {"mkdir", "write_file", "replace_text", "move"}
_FILE_DELETE_TOOLS = {"delete"}
_SERVICE_MUTATIONS = {"start_service", "restart_service", "stop_service"}
_DESTRUCTIVE_COMMAND_RULES = {
    "dangerous_command",
    "protected_path",
    "privilege_escalator",
    "control_command",
}


def execution_profile_fingerprint(settings) -> str:
    """绑定 FULL_ACCESS 获批时的实际执行边界，配置改变后必须重新复验。"""
    target_uid = os.geteuid()
    if settings.exec_user:
        try:
            target_uid = pwd.getpwnam(settings.exec_user).pw_uid
        except KeyError:
            target_uid = -1
    shell = settings.command_shell
    shell_path = shell if os.path.isabs(shell) else (shutil.which(shell) or shell)
    return canonical_fingerprint({
        "service_euid": os.geteuid(),
        "exec_user": settings.exec_user,
        "target_uid": target_uid,
        "workspace_root": str(
            Path(settings.workspace_root).expanduser().resolve(strict=False)
        ),
        "command_shell": str(Path(shell_path).resolve(strict=False)),
        "privileged_helper": settings.privileged_helper,
    })


@dataclass(frozen=True)
class ActionDescriptor:
    fingerprint: str
    capability: str
    resource: str
    mutable: bool
    destructive: bool
    paths: tuple[str, ...] = ()
    suggested_path: str = ""
    hard_block_reason: str = ""
    policy_protected: bool = False
    control_path_signal: bool = False


def _path(value) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return ""
    candidate = Path(value.strip())
    if not candidate.is_absolute():
        return ""
    try:
        return str(candidate.resolve(strict=False))
    except (OSError, RuntimeError):
        return ""


def _file_paths(step: PlanStep, tool: str) -> tuple[str, ...]:
    if tool == "move":
        values = (step.arguments.get("source"), step.arguments.get("destination"))
    else:
        values = (step.arguments.get("path"),)
    return tuple(path for value in values if (path := _path(value)))


def _command_argvs(step: PlanStep, tool: str) -> tuple[tuple[str, ...], ...]:
    """提取命令的真实 argv；run_batch 本身已是无 shell 的 argv 数组。"""
    if tool == "run_command":
        command = step.arguments.get("command")
        if not isinstance(command, str):
            return ()
        try:
            argv = shlex.split(command)
        except ValueError:
            return ()
        return (tuple(argv),) if argv else ()
    if tool != "run_batch":
        return ()
    commands = step.arguments.get("commands")
    if not isinstance(commands, list):
        return ()
    result: list[tuple[str, ...]] = []
    for argv in commands:
        if (not isinstance(argv, list) or not argv
                or any(not isinstance(value, str) for value in argv)):
            return ()
        result.append(tuple(argv))
    return tuple(result)


def _command_paths(
    step: PlanStep,
    tool: str,
    default_cwd: str = "",
) -> tuple[str, ...]:
    """保守解析 argv 中可能被命令访问的路径，用于拦截显式控制面访问。

    不尝试猜测每个 Unix 命令的完整语法；所有非选项值及 ``key=value``
    右侧都按工具调用声明的 cwd（否则配置的 workspace_root）解析。多报只会
    在值恰好指向控制面时触发产品层拒绝，覆盖 ``rm data/db``、
    ``dd of=/path`` 与批处理等常见写法。任意 shell 无法靠字符串分析形成
    真正隔离；生产环境必须使用不同 OS 账户和文件权限保护控制面。
    """
    raw_cwd = step.arguments.get("cwd")
    base = _path(raw_cwd) if isinstance(raw_cwd, str) else ""
    if not base:
        base = _path(default_cwd) if default_cwd else ""
    working_directory = Path(base) if base else Path.cwd()

    candidates: list[str] = []
    for argv in _command_argvs(step, tool):
        for argument in argv[1:]:
            values = [argument]
            if "=" in argument:
                values.append(argument.split("=", 1)[1])
            for value in values:
                if not value or value == "--" or "\x00" in value:
                    continue
                if value.startswith("-") and "=" not in value:
                    continue
                candidate = Path(value).expanduser()
                if not candidate.is_absolute():
                    candidate = working_directory / candidate
                resolved = _path(str(candidate))
                if resolved and resolved not in candidates:
                    candidates.append(resolved)
    return tuple(candidates)


def _executable_name(value: str) -> str:
    return value.replace("\\", "/").rsplit("/", 1)[-1]


def _effective_executable(argv: tuple[str, ...]) -> str:
    if not argv:
        return ""
    name = _executable_name(argv[0])
    if name in {"busybox", "toybox"} and len(argv) > 1:
        return _executable_name(argv[1])
    if name == "env":
        index = 1
        while index < len(argv):
            token = argv[index]
            if token == "--":
                index += 1
                break
            if token.startswith("-") or ("=" in token and not token.startswith("/")):
                index += 1
                continue
            break
        if index < len(argv):
            return _effective_executable(tuple(argv[index:]))
    if name in {"nice", "stdbuf", "setsid", "nohup"}:
        index = 1
        while index < len(argv) and argv[index].startswith("-"):
            if name == "nice" and argv[index] in {"-n", "--adjustment"}:
                index += 2
            else:
                index += 1
        return (_effective_executable(tuple(argv[index:]))
                if index < len(argv) else name)
    if name == "timeout":
        index = 1
        while index < len(argv) and argv[index].startswith("-"):
            if argv[index] in {"-k", "--kill-after", "-s", "--signal"}:
                index += 2
            else:
                index += 1
        return (_effective_executable(tuple(argv[index + 1:]))
                if index + 1 < len(argv) else name)
    return name


def _suggested_path(tool: str, paths: tuple[str, ...]) -> str:
    if not paths or tool in _FILE_DELETE_TOOLS:
        return ""
    target = Path(paths[-1])
    if tool == "mkdir":
        return str(target)
    return str(target.parent)


def _protected_path_reason(
    path: str, settings, *, protect_ancestors: bool = False
) -> str:
    target = Path(path)
    candidates: list[tuple[Path, str]] = []
    db_path = Path(settings.db_path).resolve(strict=False)
    candidates.extend([
        (db_path, "审计与认证数据库属于控制面"),
        (Path(f"{db_path}-wal"), "审计数据库 WAL 属于控制面"),
        (Path(f"{db_path}-shm"), "审计数据库共享内存属于控制面"),
        (Path("/etc/kylinguard"), "服务配置与密钥目录属于控制面"),
        (Path("/usr/local/libexec/kylinguard"), "特权 helper 属于控制面"),
    ])
    root_env = Path(__file__).resolve().parents[2] / ".env"
    candidates.append((root_env.resolve(strict=False), "LLM 与管理员密钥文件受保护"))
    for protected, reason in candidates:
        try:
            # 既禁止直接触碰控制面，也禁止删除/移动它的祖先目录。例如删除
            # /var/lib/kylinguard 会一并移除其中的审计 DB，不能因为目标不是
            # DB 的“子路径”而漏过检查。
            if (target == protected
                    or target.is_relative_to(protected)
                    or (protect_ancestors and protected.is_relative_to(target))):
                return reason
        except (OSError, ValueError):
            continue
    return ""


def describe_action(
    step: PlanStep,
    meta: ToolMeta,
    rule: RuleVerdict,
    settings,
    protected_prefixes: tuple[str, ...] = (),
) -> ActionDescriptor:
    """生成授权绑定的动作清单；指纹使用真实参数，展示资源会脱敏。"""
    server, _, tool = step.tool.partition(".")
    paths: tuple[str, ...] = ()
    mutable = meta.risk != RiskLevel.LOW
    destructive = meta.risk == RiskLevel.HIGH
    capability = f"{server}.{tool}"
    resource = ""
    suggested = ""

    if server == "files":
        paths = _file_paths(step, tool)
        resource = " → ".join(paths)
        suggested = _suggested_path(tool, paths)
        if tool in _FILE_READ_TOOLS:
            capability, mutable, destructive = "files.read", False, False
        elif tool in _FILE_WRITE_TOOLS:
            capability, mutable, destructive = "files.write", True, False
        elif tool in _FILE_DELETE_TOOLS:
            capability, mutable, destructive = "files.delete", True, True
    elif server == "run_command":
        capability = (
            "command.read" if rule.decision == RuleDecision.ALLOW
            else "command.execute"
        )
        mutable = rule.decision != RuleDecision.ALLOW
        # 解释器、下载器等 payload_executor 只说明命令可以承载二级动作，
        # 不能据此一概判为破坏性；真正的高危由命中规则或有效可执行文件决定。
        destructive = rule.matched_rule in _DESTRUCTIVE_COMMAND_RULES
        argvs = _command_argvs(step, tool)
        paths = _command_paths(
            step, tool, getattr(settings, "workspace_root", ""))
        command = step.arguments.get("command")
        if isinstance(command, str):
            resource = redact_text(command)
        else:
            executables = [_effective_executable(argv) for argv in argvs if argv]
            resource = "argv batch: " + " ; ".join(executables)
        for argv in argvs:
            executable = _effective_executable(argv)
            destructive = destructive or executable in {
                "rm", "truncate", "dd", "chmod", "chown", "kill",
                "pkill", "killall",
            }
        # 未证明只读的命令若把文件系统根作为参数，必须按破坏性动作处理。
        # 这同时封住 nice/timeout/setsid 等包装器隐藏 rm 的长尾别名。
        if capability == "command.execute":
            destructive = destructive or any(
                Path(path).parent == Path(path) for path in paths
            )
    elif server == "services" and tool in _SERVICE_MUTATIONS:
        capability = f"service.{tool.removesuffix('_service')}"
        mutable = True
        destructive = tool == "stop_service"
        resource = str(step.arguments.get("name", ""))
    elif server == "disk" and tool == "clean_file":
        capability, mutable, destructive = "files.delete", True, True
        paths = _file_paths(step, tool)
        resource = " → ".join(paths)

    policy_protected = bool(
        mutable
        and paths
        and any(
            _within_root(path, prefix)
            for path in paths
            for prefix in protected_prefixes
            if prefix
        )
    )
    if policy_protected:
        destructive = True

    hard_reason = ""
    control_path_signal = False
    protect_ancestors = destructive or (server == "files" and tool == "move")
    for path in paths:
        if reason := _protected_path_reason(
            path, settings, protect_ancestors=protect_ancestors
        ):
            if server == "run_command":
                # shell 参数路径只能保守猜测：把它作为高风险信号，不能用
                # 会误杀/可绕过的字符串分析伪装成隔离边界。
                control_path_signal = True
                mutable = True
                destructive = True
                capability = "command.execute"
            else:
                # 结构化文件工具的路径是精确参数，可以可靠阻止直接读写控制面。
                hard_reason = f"拒绝访问 {path}：{reason}。"
            break
    fingerprint = canonical_fingerprint({
        "tool": step.tool,
        "arguments": step.arguments,
        "capability": capability,
    })
    return ActionDescriptor(
        fingerprint=fingerprint,
        capability=capability,
        resource=resource,
        mutable=mutable,
        destructive=destructive,
        paths=paths,
        suggested_path=suggested,
        hard_block_reason=hard_reason,
        policy_protected=policy_protected,
        control_path_signal=control_path_signal,
    )


def _within_root(path: str, root: str) -> bool:
    try:
        normalized_path = os.path.normcase(str(Path(path).resolve(strict=False)))
        normalized_root = os.path.normcase(str(Path(root).resolve(strict=False)))
        return os.path.commonpath([normalized_path, normalized_root]) == normalized_root
    except (OSError, RuntimeError, ValueError):
        return False


def trusted_workspace_allows(
    context: SessionPermissionContext,
    action: ActionDescriptor,
) -> bool:
    if action.destructive or action.capability != "files.write" or not action.paths:
        return False
    return all(
        any(_within_root(path, root) for root in context.trusted_roots)
        for path in action.paths
    )


def apply_permission_mode(
    context: SessionPermissionContext,
    action: ActionDescriptor,
    base: GateDecision,
    *,
    has_grant: bool = False,
    grant_scope: PermissionGrantScope | None = None,
) -> GateDecision:
    """在风险裁决上应用会话权限。

    FULL_ACCESS 是管理员复验后的绝对产品权限：它可以覆盖精确路径等产品层
    限制，避免结构化文件工具与 shell 出现路线依赖。协议/参数无效等规则层
    hard deny 仍由 ``base.action == DENY`` 保持不可执行。
    """
    mode = PermissionMode.ASK if context.expired else context.mode
    if action.hard_block_reason and mode != PermissionMode.FULL_ACCESS:
        return GateDecision(
            action=GateAction.DENY,
            risk=RiskLevel.HIGH,
            reason=action.hard_block_reason,
        )
    # gate 只会把确定性硬规则收敛为 DENY。权限模式不能覆盖该结论；
    # Reviewer 的负面意见则已被 gate 转换为可确认的高风险告警。
    if base.action == GateAction.DENY:
        return base

    if action.destructive:
        base = GateDecision(
            action=GateAction.DOUBLE_CONFIRM,
            risk=RiskLevel.HIGH,
            reason=f"{base.reason}；该动作具有删除、提权或不可逆副作用。",
        )

    # 完全访问覆盖 Reviewer 告警、路径策略和产品层确认；风险级别与告警
    # 原因仍保留在审计中。规则层协议 hard deny 已在上方保持拒绝。
    if mode == PermissionMode.FULL_ACCESS:
        return GateDecision(
            action=GateAction.AUTO,
            risk=base.risk,
            reason=(
                "当前会话已由管理员启用完全访问；产品层不再限制该动作，"
                "仍受执行账户 OS 权限约束。"
            ),
        )
    if not action.mutable:
        return base
    if mode == PermissionMode.READ_ONLY:
        return GateDecision(
            action=GateAction.DENY,
            risk=base.risk,
            reason="当前会话处于只读模式，修改操作不会执行。",
        )
    if has_grant and (
        not action.destructive or grant_scope == PermissionGrantScope.ONCE
    ):
        return GateDecision(
            action=GateAction.AUTO,
            risk=base.risk,
            reason="该标准化动作已获得当前会话授权。",
        )
    if (mode == PermissionMode.TRUSTED_WORKSPACE
            and trusted_workspace_allows(context, action)):
        return GateDecision(
            action=GateAction.AUTO,
            risk=base.risk,
            reason="目标位于本会话可信目录，允许自动创建或修改。",
        )
    return base


__all__ = [
    "ActionDescriptor",
    "apply_permission_mode",
    "describe_action",
    "execution_profile_fingerprint",
    "trusted_workspace_allows",
]
