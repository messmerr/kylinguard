"""感知阶段：主动采集系统快照注入 LLM 上下文，减少幻觉。

全部为只读命令；逐项独立采集，单项失败降级为错误说明，绝不抛出。
"""
from kylinguard.executor import run_command

_SNAPSHOT_COMMANDS: dict[str, str] = {
    "uptime_load": "uptime",
    "memory": "free -m",
    "disk": "df -h",
    "top_cpu": "ps aux --sort=-%cpu",
    "failed_units": "systemctl --failed --no-pager --plain",
    "recent_errors": "journalctl -p err -n 20 --no-pager",
}

_TITLES = {
    "uptime_load": "运行时长与负载", "memory": "内存(MB)", "disk": "磁盘",
    "top_cpu": "CPU 占用最高进程", "failed_units": "失败的服务",
    "recent_errors": "近期错误日志",
}


async def collect_snapshot() -> dict[str, str]:
    result: dict[str, str] = {}
    for key, cmd in _SNAPSHOT_COMMANDS.items():
        r = await run_command(cmd, timeout=10, max_output=4096)
        if r.exit_code == 0:
            result[key] = r.stdout.strip() or "(无输出)"
        else:
            result[key] = f"[采集失败] {(r.stderr or r.stdout).strip()}"
    return result


def format_snapshot(snapshot: dict[str, str], per_item: int = 2000) -> str:
    parts = []
    for key, value in snapshot.items():
        title = _TITLES.get(key, key)
        body = value[:per_item]
        parts.append(f"### {title}（{key}）\n{body}")
    return "\n\n".join(parts)
