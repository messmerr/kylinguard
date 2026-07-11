"""规则引擎（三道闸第一道）：对 run_command 自由命令做静态判定。

设计参照 Codex CLI execpolicy 与 Claude Code 权限模型的调研结论：
- argv 级匹配（shlex 拆分）而非对 shell 字符串做正则前缀——不易被
  引号/空格/选项前置等变体绕过；
- 只读白名单是"命令+参数"级而非命令名级：白名单命令携带危险 flag
  （tail -f、date -s、ip link set）即失去只读资格；
- "执行其参数"的载荷执行器绝不凭前缀放行：提权/子 shell/解释器/远程下载
  等载荷运行器直接拒绝；
- 任何无法解析的输入 fail closed（拒绝），绝不猜测。

执行器不经 shell（argv 直接 exec），元字符本无法生效，
但出现元字符本身即是逃逸尝试信号，直接拒绝并留痕。
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

# --- 黑名单（字符串级粗筛）：无条件拒绝 ---
_BLACKLIST: list[tuple[str, str]] = [
    (r"\bdd\b.*\bof=/dev/", "直接写块设备"),
    (r"\bmkfs(\.\w+)?\b", "格式化文件系统"),
    (r":\s*\(\s*\)\s*\{", "fork 炸弹"),
    (r"\bchmod\s+(-R\s+)?0?777\s+/(\s|$)", "开放根目录全权限"),
    (r"\bhalt\b|\bpoweroff\b|\binit\s+0\b", "关闭主机"),
]

# --- shell 元字符（命令注入/逃逸信号） ---
_METACHARS = re.compile(r"[;&|`<>]|\$\(")

# --- 提权/子 shell 执行器：模型自行提权或起子 shell 即为越权信号，直接拒绝 ---
_PRIVILEGE_ESCALATORS = {
    "sudo", "su", "pkexec", "runuser", "chroot", "nsenter", "setpriv",
    "sh", "bash", "zsh", "dash", "ksh", "csh", "fish",
}

# --- 载荷执行器：可把参数、远端内容或脚本解释成动作，run_command 中一律拒绝 ---
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
    "uname": set(), "whoami": set(), "id": set(), "hostname": set(),
    "pwd": set(), "ls": set(), "wc": set(), "which": set(), "stat": set(),
    "nl": set(), "cut": set(), "diff": set(), "last": set(), "lastb": set(),
    "lastlog": set(),
    "lsblk": set(), "lscpu": set(), "lsmem": set(), "ss": set(),
    "netstat": set(), "cat": set(), "head": set(), "grep": set(),
    "echo": set(),
    "tail": {"f", "F", "--follow"},
    "date": {"s", "--set"},
    "journalctl": {"f", "--follow"},
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
    """展开常见 multicall/环境包装器，仅用于识别不可覆盖的硬红线。"""
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


def check_command(command: str,
                  extra: ExtraPolicies | None = None) -> RuleVerdict:
    text = command.strip()
    if not text:
        return _deny("空命令，拒绝执行", "empty")

    # ① 黑名单粗筛（字符串级，覆盖 fork 炸弹等 argv 拆不出的模式）
    #    自定义黑名单与内置合并（只收紧）
    custom_blacklist = extra.blacklist if extra else []
    for pattern, label in list(_BLACKLIST) + list(custom_blacklist):
        if re.search(pattern, text):
            return _deny(f"命中危险命令黑名单：{label}", pattern)

    # ② shell 元字符：执行器不经 shell 本就无效，出现即是逃逸尝试
    m = _METACHARS.search(text)
    if m:
        return _deny(
            f"包含 shell 元字符 {m.group()!r}（逃逸/注入信号）；"
            "请改用单条简单命令或结构化插件工具", "metachar")

    # ③ argv 拆分：解析失败 fail closed
    try:
        argv = shlex.split(text)
    except ValueError as e:
        return _deny(f"命令无法安全解析（{e}），按安全原则拒绝", "unparseable")
    if not argv:
        return _deny("空命令，拒绝执行", "empty")
    argv[0] = _basename(argv[0])

    # ④ argv 级黑名单：递归删根（字符串正则易被空格/选项顺序绕过）
    if _is_rm_root(argv):
        return _deny("命中危险命令黑名单：递归删除根目录", "rm_root")

    # ⑤ 提权/子 shell 执行器：一律拒绝（提权仅由受限执行器统一管理）
    if argv[0] in _PRIVILEGE_ESCALATORS:
        return _deny(
            f"禁止经 {argv[0]!r} 提权或启动子 shell；"
            "提权由系统受限执行器统一管理", "privilege_escalator",
            hard=False)

    if argv[0] in _PAYLOAD_EXECUTORS:
        return _deny(
            f"禁止经 {argv[0]!r} 执行脚本、远端载荷或二级命令；"
            "请改用受控 MCP 插件工具", "payload_executor", hard=False)

    # ⑤ 保护路径写操作：安全红线（自定义保护路径合并）
    protected = _PROTECTED_PREFIXES + (extra.protected if extra else ())
    writes = argv[0] in _WRITE_COMMANDS or (
        argv[0] in _INPLACE_EDITORS
        and any(t == "-i" or t.startswith("-i.") or t == "--in-place"
                for t in argv[1:])
    )
    touches = any(arg.startswith(protected) for arg in argv[1:])
    builtin_whitelisted = _is_readonly_whitelisted(argv)
    custom_declared_readonly = (
        extra is not None and argv[0] in extra.readonly
    )
    if writes and touches:
        return _deny("疑似修改关键（保护）配置文件，安全红线禁止",
                     "protected_path")

    if writes:
        return _deny(
            f"自由命令禁止执行写操作 {argv[0]!r}；"
            "请改用具备参数约束和白名单的结构化 MCP 插件", "mutating_command",
            hard=False)

    if argv[0] in _CONTROL_COMMANDS or _is_systemctl_mutation(argv):
        return _deny(
            f"自由命令禁止执行控制型操作 {argv[0]!r}；"
            "服务启停等动作必须走结构化 MCP 插件和最小权限代理", "control_command",
            hard=False)

    if argv[0] in _INPLACE_EDITORS and not builtin_whitelisted:
        return _deny(
            f"自由命令禁止执行解释/编辑器类命令 {argv[0]!r}；"
            "请改用只读工具或结构化插件", "editor_command", hard=False)

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

    # ⑦ 其余未知命令：不靠“确认”放行。需要扩展能力时应新增结构化插件或只读白名单。
    return RuleVerdict(
        decision=RuleDecision.DENY,
        reason="未知自由命令未进入只读白名单，需显式权限或结构化 MCP 插件",
        matched_rule="unknown_command",
        hard=False,
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
