"""run_command 的静态风险分类器。

执行能力和授权决策是两个正交维度：执行器提供完整 shell；本模块只证明
简单只读命令可以自动执行，或把其余命令标成需要复核/确认。管道、重定向、
解释器、网络工具、提权和未知命令都是真实能力，不能仅因命令类别被永久
阉割。只有空输入、NUL、管理员显式黑名单等协议或组织边界才硬拒绝。

对简单命令仍采用 argv 级匹配，避免只读白名单被危险 flag 绕过。无法可靠
解析的完整 shell 程序交给 Reviewer 与权限模式，而不是假装静态分析能够理解
任意 Bash 语义。
"""
import re
import shlex
from dataclasses import dataclass, field

from kylinguard.models import RuleDecision, RuleVerdict


@dataclass(frozen=True)
class ExtraPolicies:
    """管理员自定义策略（PolicyStore 聚合产出），与内置规则合并判定。"""
    blacklist: list = field(default_factory=list)   # [(正则, 说明)]
    readonly: frozenset = frozenset()               # 命令名集合
    protected: tuple = ()                           # 路径前缀

# --- 内置灾难性模式：提升为高风险，但可由显式权限模式授权 ---
_BLACKLIST: list[tuple[str, str]] = [
    (r"\bdd\b.*\bof=/dev/", "直接写块设备"),
    (r"\bmkfs(\.\w+)?\b", "格式化文件系统"),
    (r":\s*\(\s*\)\s*\{", "fork 炸弹"),
    (r"\bchmod\s+(-R\s+)?0?777\s+/(\s|$)", "开放根目录全权限"),
    (r"\bhalt\b|\bpoweroff\b|\binit\s+0\b", "关闭主机"),
]

# --- 需要完整 shell 解释的语法；它是能力信号，不是攻击判据 ---
_METACHARS = re.compile(
    r"[;&|`<>\r\n*?\[\]{}$~#()]"
)

# --- 提权/子 shell：按高风险或需复核分类，不再永久禁用 ---
_PRIVILEGE_ESCALATORS = {
    "sudo", "su", "pkexec", "runuser", "chroot", "nsenter", "setpriv",
    "sh", "bash", "zsh", "dash", "ksh", "csh", "fish",
}

# --- 载荷执行器：无法静态证明只读，交给后续风险与权限门控 ---
_PAYLOAD_EXECUTORS = {
    "env", "xargs", "ssh", "scp", "sftp", "docker", "podman", "kubectl",
    "systemd-run", "crontab", "at", "nohup", "watch", "python", "python3",
    "perl", "ruby", "node", "php", "lua", "curl", "wget", "nc", "ncat",
    "socat", "base64", "openssl",
}

# --- 关键配置文件/目录（安全红线）：写操作拒绝，读操作降级交后续闸门 ---
_PROTECTED_PREFIXES = (
    "/etc/passwd", "/etc/shadow", "/etc/gshadow", "/etc/sudoers",
    "/etc/group", "/etc/hosts", "/etc/ssh/", "/etc/pam.d", "/etc/systemd/",
    "/etc/cron", "/etc/kylin-release", "/boot/", "/root/.ssh/",
    "/usr/lib/systemd/", "/lib/systemd/", "/var/spool/cron",
)
# 写型命令：参数触及保护路径即视为修改企图
_WRITE_COMMANDS = {
    "rm", "mv", "cp", "tee", "truncate", "chown", "chmod", "ln",
    "touch", "dd", "install", "rsync",
}
# sed/awk 仅带原地编辑 flag 时算写
_INPLACE_EDITORS = {"sed", "awk", "gawk", "perl"}

_CONTROL_COMMANDS = {
    "kill", "pkill", "killall", "reboot", "shutdown", "halt", "poweroff",
    "mount", "umount", "swapoff", "swapon", "service",
}
_SYSTEMCTL_MUTATING_SUBCMDS = {
    "start", "restart", "stop", "reload", "try-restart", "reload-or-restart",
    "enable", "disable", "mask", "unmask", "reset-failed", "set-property",
    "edit", "link", "preset", "preset-all", "add-wants", "add-requires",
    "revert",
}

# --- 只读白名单：命令名 → 使其失格的危险 flag 集合 ---
#（单字符项按短选项字符匹配，"--xx" 项按长选项精确匹配）
_SAFE_COMMANDS: dict[str, set[str]] = {
    "ps": set(), "free": set(), "df": set(), "du": set(), "uptime": set(),
    "uname": set(), "whoami": set(), "id": set(),
    "pwd": set(), "ls": set(), "wc": set(), "which": set(), "stat": set(),
    "nl": set(), "cut": set(), "diff": {"--output"},
    "last": set(), "lastb": set(),
    "lastlog": {"C", "S", "--clear", "--set"},
    "lsblk": set(), "lscpu": set(), "lsmem": set(),
    "ss": {"K", "--kill"},
    "netstat": set(), "cat": set(), "head": set(), "grep": set(),
    "echo": set(),
    "tail": {"f", "F", "--follow"},
    "date": {"s", "--set"},
    # journalctl 的维护操作会轮转、删除或改写持久日志；即使当前账户
    # 最终没有权限，也不能把它们证明成只读并在 READ_ONLY 下自动执行。
    "journalctl": {
        "f", "--follow", "--rotate", "--vacuum-size", "--vacuum-time",
        "--vacuum-files", "--sync", "--flush", "--relinquish-var",
        "--smart-relinquish-var", "--update-catalog", "--setup-keys",
    },
}

_SYSTEMCTL_RO_SUBCMDS = {
    "status", "list-units", "list-unit-files", "list-timers",
    "is-active", "is-enabled", "is-failed", "show", "cat",
}
_IP_RO_OBJECTS = {"a", "addr", "address", "route", "link", "neigh", "-s"}
_IP_MUTATING_VERBS = {"set", "add", "del", "delete", "flush", "replace", "change"}


def _deny(reason: str, rule: str, *, hard: bool = True) -> RuleVerdict:
    return RuleVerdict(decision=RuleDecision.DENY, reason=reason,
                       matched_rule=rule, hard=hard)


def _has_forbidden_flag(argv: list[str], forbidden: set[str]) -> bool:
    if not forbidden:
        return False
    for tok in argv[1:]:
        if tok.startswith("--"):
            if tok.split("=", 1)[0] in forbidden:
                return True
        elif tok.startswith("-") and len(tok) > 1:
            if any(ch in forbidden for ch in tok[1:]):
                return True
    return False


def _is_rm_root(argv: list[str]) -> bool:
    """argv 级判定"递归删除根目录"，不受空格/选项顺序变体影响。"""
    effective = _effective_argv(argv)
    if not effective or effective[0] != "rm":
        return False
    short_flags = set("".join(
        t[1:] for t in effective[1:]
        if t.startswith("-") and not t.startswith("--")))
    recursive = (bool(short_flags & {"r", "R"})
                 or "--recursive" in effective[1:])
    hits_root = any(re.fullmatch(r"/+", t)
                    for t in effective[1:] if not t.startswith("-"))
    return recursive and hits_root


def _basename(value: str) -> str:
    # PurePath on Windows does not recognize '/' as the active separator；命令
    # 最终运行在 Linux/WSL，显式兼容两种分隔符。
    return value.replace("\\", "/").rsplit("/", 1)[-1]


def _effective_argv(argv: list[str]) -> list[str]:
    """展开常见 multicall/环境包装器，用于识别灾难性风险信号。"""
    if not argv:
        return []
    values = [_basename(argv[0]), *argv[1:]]
    if values[0] in {"busybox", "toybox"} and len(values) > 1:
        return [_basename(values[1]), *values[2:]]
    if values[0] == "env":
        index = 1
        while index < len(values):
            token = values[index]
            if token == "--":
                index += 1
                break
            if token.startswith("-") or ("=" in token and not token.startswith("/")):
                index += 1
                continue
            break
        if index < len(values):
            return _effective_argv(values[index:])
    if values[0] in {"nice", "stdbuf", "setsid", "nohup"}:
        index = 1
        while index < len(values) and values[index].startswith("-"):
            if values[0] == "nice" and values[index] in {"-n", "--adjustment"}:
                index += 2
            else:
                index += 1
        if index < len(values):
            return _effective_argv(values[index:])
    if values[0] == "timeout":
        index = 1
        while index < len(values) and values[index].startswith("-"):
            # -k/--kill-after 与 -s/--signal 各自还消费一个参数。
            if values[index] in {"-k", "--kill-after", "-s", "--signal"}:
                index += 2
            else:
                index += 1
        if index + 1 < len(values):
            return _effective_argv(values[index + 1:])
    return values


def _is_readonly_whitelisted(argv: list[str]) -> bool:
    cmd = argv[0]
    # `hostname` 无参数只读；位置参数和 -F/--file 会修改主机名。
    if cmd == "hostname":
        return len(argv) == 1
    if cmd in _SAFE_COMMANDS:
        return not _has_forbidden_flag(argv, _SAFE_COMMANDS[cmd])
    if cmd == "systemctl":
        words = [t for t in argv[1:] if not t.startswith("-")]
        flags = [t for t in argv[1:] if t.startswith("-")]
        if "--failed" in flags and not words:
            return True
        return bool(words) and words[0] in _SYSTEMCTL_RO_SUBCMDS
    if cmd == "ip":
        words = [t for t in argv[1:] if not t.startswith("-")]
        if not words or words[0] not in _IP_RO_OBJECTS:
            return False
        return not any(w in _IP_MUTATING_VERBS for w in words[1:])
    return False


def _is_systemctl_mutation(argv: list[str]) -> bool:
    if argv[0] != "systemctl":
        return False
    words = [t for t in argv[1:] if not t.startswith("-")]
    return bool(words) and words[0] in _SYSTEMCTL_MUTATING_SUBCMDS


def _is_system_mutation(argv: list[str]) -> bool:
    """识别白名单命令中确定会改变系统状态的参数形式。"""
    command = argv[0]
    if command == "ss":
        return _has_forbidden_flag(argv, {"K", "--kill"})
    if command == "journalctl":
        return _has_forbidden_flag(argv, {
            "--rotate", "--vacuum-size", "--vacuum-time", "--vacuum-files",
            "--sync", "--flush", "--relinquish-var",
            "--smart-relinquish-var", "--update-catalog", "--setup-keys",
        })
    if command == "lastlog":
        return _has_forbidden_flag(argv, {"C", "S", "--clear", "--set"})
    if command == "date":
        return _has_forbidden_flag(argv, {"s", "--set"})
    if command == "hostname":
        return len(argv) > 1
    if command == "ip":
        words = [token for token in argv[1:] if not token.startswith("-")]
        return any(word in _IP_MUTATING_VERBS for word in words)
    return False


def check_command(
    command: str,
    extra: ExtraPolicies | None = None,
    *,
    _scan_shell_syntax: bool = True,
) -> RuleVerdict:
    text = command.strip()
    if not text:
        return _deny("空命令，拒绝执行", "empty")
    if "\x00" in text:
        return _deny("命令包含 NUL，无法交给操作系统执行", "nul")

    # ① 自定义/内置模式负责提升风险。完整 Bash 可以通过展开、变量与解释器
    #    间接表达同一动作，因此字符串规则不能冒充不可绕过的组织级沙箱。
    #    ASK 模式会要求显式授权，FULL_ACCESS 则按其真实语义直接执行。
    custom_blacklist = extra.blacklist if extra else []
    for pattern, label in custom_blacklist:
        if re.search(pattern, text):
            return _deny(
                f"命中管理员自定义高风险规则：{label}",
                f"custom:{pattern}", hard=False,
            )
    for pattern, label in _BLACKLIST:
        if re.search(pattern, text):
            return _deny(
                f"检测到灾难性命令：{label}；需要显式高权限授权",
                "dangerous_command", hard=False,
            )

    # ② argv 拆分只用于风险分类。Bash 能合法接受的多行、替换和复杂引号
    #    不必先被 Python shlex 完整理解。
    try:
        argv = shlex.split(text)
    except ValueError as e:
        return RuleVerdict(
            decision=RuleDecision.REVIEW,
            reason=f"完整 shell 语法无法由静态分类器展开（{e}），交由权限复核",
            matched_rule="shell_expression",
            hard=False,
        )
    if not argv:
        return _deny("空命令，拒绝执行", "empty")
    executable_has_path = "/" in argv[0] or "\\" in argv[0]
    argv[0] = _basename(argv[0])

    # ③ argv 级识别递归删根（字符串正则易被选项顺序与包装器绕过）。
    if _is_rm_root(argv):
        return _deny(
            "检测到递归删除根目录；需要显式高权限授权",
            "dangerous_command", hard=False,
        )

    # ④ 完整 shell 程序不能靠首个 argv 证明只读。允许执行，但至少进入
    #    Reviewer/权限层；这覆盖管道、重定向、变量、替换、串联和 heredoc。
    m = _METACHARS.search(text) if _scan_shell_syntax else None
    if m:
        return _deny(
            (f"命令使用完整 shell 语法 {m.group()!r}，"
             "需要显式权限；该语法本身仍由终端完整支持"),
            "shell_expression",
            hard=False,
        )

    # ⑤ 提权/子 shell 是高风险能力，但不是永久禁用的能力。
    if argv[0] in _PRIVILEGE_ESCALATORS:
        return _deny(
            f"{argv[0]!r} 会提权或启动二级 shell，需要高权限授权",
            "privilege_escalator",
            hard=False)

    if argv[0] in _PAYLOAD_EXECUTORS:
        return _deny(
            (f"{argv[0]!r} 可执行脚本、远端操作或二级命令，"
             "需要显式权限"),
            "payload_executor",
            hard=False,
        )

    # ⑤ 保护路径写操作：安全红线（自定义保护路径合并）
    protected = _PROTECTED_PREFIXES + (extra.protected if extra else ())
    writes = argv[0] in _WRITE_COMMANDS or (
        argv[0] in _INPLACE_EDITORS
        and any(t == "-i" or t.startswith("-i.") or t == "--in-place"
                for t in argv[1:])
    )
    touches = any(arg.startswith(protected) for arg in argv[1:])
    # 只读自动通道只信任固定系统 PATH 中的裸命令名。显式 /tmp/ps、./cat
    # 或同名 symlink 不能因为 basename 命中白名单就获得自动执行资格。
    builtin_whitelisted = (
        not executable_has_path and _is_readonly_whitelisted(argv)
    )
    custom_declared_readonly = (
        extra is not None and argv[0] in extra.readonly
    )
    if writes and touches:
        return _deny(
            "疑似修改关键系统配置，需要显式高权限授权",
            "protected_path", hard=False,
        )

    if argv[0] == "diff" and _has_forbidden_flag(argv, {"--output"}):
        return _deny(
            "diff --output 会创建或覆盖文件，需要显式权限",
            "mutating_command", hard=False,
        )

    if writes:
        return _deny(
            f"命令 {argv[0]!r} 可能修改文件，需要显式权限",
            "mutating_command",
            hard=False)

    if (argv[0] in _CONTROL_COMMANDS
            or _is_systemctl_mutation(argv)
            or _is_system_mutation(argv)):
        return _deny(
            f"命令 {argv[0]!r} 会控制系统或进程，需要高权限授权",
            "control_command",
            hard=False)

    if argv[0] in _INPLACE_EDITORS and not builtin_whitelisted:
        return _deny(
            f"命令 {argv[0]!r} 可能解释或编辑内容，需要显式权限",
            "editor_command", hard=False)

    # ⑥ 只读白名单（命令+参数级）；触及保护路径的读操作降级交后续闸门
    #    自定义白名单为命令名级（管理员显式放行决策）
    if builtin_whitelisted:
        if touches:
            return RuleVerdict(
                decision=RuleDecision.REVIEW,
                reason="只读命令但访问敏感文件，交由 LLM 审查员与风险门控判定")
        return RuleVerdict(decision=RuleDecision.ALLOW,
                           reason="命中只读命令白名单（命令与参数均只读）",
                           matched_rule=argv[0])

    # 旧版自定义“只读白名单”只有命令名，没有参数语义。把 git/find 等命令
    # 按名称直接自动放行会让 --force、-delete 等写型参数绕过校验。保留其
    # 管理员意图，但只降到人工/权限引擎复核，不能再视为确定的只读操作。
    if custom_declared_readonly:
        return RuleVerdict(
            decision=RuleDecision.REVIEW,
            reason="管理员将该命令标为可信，但规则无法证明参数只读，需权限复核",
            matched_rule=f"custom:{argv[0]}",
        )

    # ⑦ 未知命令仍然是合法能力：不能证明只读，因此交由后续层确认。
    return RuleVerdict(
        decision=RuleDecision.REVIEW,
        reason="静态层无法证明该命令只读，交由风险与权限复核",
        matched_rule="unknown_command",
        hard=False,
    )


def check_argv(
    argv: list[str],
    extra: ExtraPolicies | None = None,
) -> RuleVerdict:
    """分类已经结构化的精确 argv，不把字面元字符误当成 shell 语法。"""
    if (not isinstance(argv, list) or not argv
            or any(not isinstance(value, str) or "\x00" in value
                   for value in argv)
            or not argv[0]):
        return _deny("结构化 argv 不合法", "invalid_argv")
    return check_command(
        shlex.join(argv), extra=extra, _scan_shell_syntax=False,
    )


def builtin_rules() -> dict:
    """导出内置规则供策略管理页只读展示（代码级安全基线，UI 不可改）。"""
    return {
        "blacklist": [(p, label) for p, label in _BLACKLIST]
                     + [("<argv>rm -r 目标为 /", "递归删除根目录（argv 级判定）")],
        "metachars": _METACHARS.pattern,
        "privilege_escalators": sorted(_PRIVILEGE_ESCALATORS),
        "payload_executors": sorted(_PAYLOAD_EXECUTORS),
        "protected_prefixes": list(_PROTECTED_PREFIXES),
        "write_commands": sorted(_WRITE_COMMANDS),
        "control_commands": sorted(_CONTROL_COMMANDS),
        "safe_commands": {cmd: sorted(flags)
                          for cmd, flags in _SAFE_COMMANDS.items()},
        "systemctl_ro_subcmds": sorted(_SYSTEMCTL_RO_SUBCMDS),
    }
