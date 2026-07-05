"""感知阶段：主动采集系统快照注入 LLM 上下文，减少幻觉。

全部为只读命令；逐项独立采集，单项失败降级为错误说明，绝不抛出。
SnapshotCache 后台定时刷新，请求路径零采集延迟；该缓存同时是
仪表盘（M2）的数据源。
"""
import asyncio
import sys
import time

from kylinguard.executor import run_command

_IS_WINDOWS = sys.platform == "win32"

# Windows 下用 argv 列表避免 shlex.split 破坏 PowerShell 脚本
_PS = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"]

_SNAPSHOT_COMMANDS_WINDOWS: dict[str, list[str]] = {
    "uptime_load": _PS + [
        "$up=(Get-Date)-(Get-CimInstance Win32_OperatingSystem).LastBootUpTime;"
        "$load=(Get-CimInstance Win32_Processor|Measure-Object LoadPercentage -Average).Average;"
        "'{0}d {1}h {2}m  CPU: {3}%' -f $up.Days,$up.Hours,$up.Minutes,[int]$load"
    ],
    "memory": _PS + [
        "$os=Get-CimInstance Win32_OperatingSystem;"
        "$t=[math]::Round($os.TotalVisibleMemorySize/1KB);"
        "$f=[math]::Round($os.FreePhysicalMemory/1KB);"
        "'total={0}MB used={1}MB free={2}MB' -f $t,($t-$f),$f"
    ],
    "disk": _PS + [
        "Get-PSDrive -PSProvider FileSystem|Where-Object{$_.Used -ne $null}|"
        "ForEach-Object{"
        "$t=[math]::Round(($_.Used+$_.Free)/1GB,1);"
        "$u=[math]::Round($_.Used/1GB,1);"
        "$fr=[math]::Round($_.Free/1GB,1);"
        "'{0,-4} total={1}G used={2}G free={3}G' -f $_.Name,$t,$u,$fr}"
    ],
    "top_cpu": _PS + [
        "Get-Process|Sort-Object CPU -Descending|Select-Object -First 15|"
        "Format-Table -AutoSize Name,Id,"
        "@{n='CPU(s)';e={[math]::Round($_.CPU,1)}},"
        "@{n='Mem(MB)';e={[math]::Round($_.WorkingSet/1MB,1)}}|Out-String"
    ],
    "failed_units": _PS + [
        "Get-Service|Where-Object{$_.Status -eq 'Stopped' -and $_.StartType -eq 'Automatic'}|"
        "Select-Object Name,DisplayName,Status|Format-Table -AutoSize|Out-String"
    ],
    "recent_errors": _PS + [
        "Get-WinEvent -FilterHashtable @{LogName='System';Level=2} -MaxEvents 20 "
        "-ErrorAction SilentlyContinue|"
        "ForEach-Object{'['+$_.TimeCreated.ToString('MM-dd HH:mm')+'] '+$_.ProviderName+': '+($_.Message -split \"`n\")[0]}"
    ],
}

_SNAPSHOT_COMMANDS_LINUX: dict[str, str] = {
    "uptime_load": "uptime",
    "memory": "free -m",
    "disk": "df -h",
    "top_cpu": "ps aux --sort=-%cpu",
    "failed_units": "systemctl --failed --no-pager --plain",
    "recent_errors": "journalctl -p err -n 20 --no-pager",
}

_SNAPSHOT_COMMANDS: dict = (
    _SNAPSHOT_COMMANDS_WINDOWS if _IS_WINDOWS else _SNAPSHOT_COMMANDS_LINUX
)

_TITLES = {
    "uptime_load": "运行时长与负载", "memory": "内存(MB)", "disk": "磁盘",
    "top_cpu": "CPU 占用最高进程", "failed_units": "失败的服务",
    "recent_errors": "近期错误日志",
}


async def _collect_one(key: str, cmd: str | list[str]) -> tuple[str, str]:
    if isinstance(cmd, list):
        r = await run_command(cmd, timeout=15, max_output=4096)
    else:
        r = await run_command(cmd, timeout=10, max_output=4096)
    if r.exit_code == 0:
        return key, r.stdout.strip() or "(无输出)"
    return key, f"[采集失败] {(r.stderr or r.stdout).strip()}"


async def collect_snapshot() -> dict[str, str]:
    pairs = await asyncio.gather(
        *(_collect_one(k, c) for k, c in _SNAPSHOT_COMMANDS.items()))
    return dict(pairs)


def format_snapshot(snapshot: dict[str, str], per_item: int = 2000) -> str:
    parts = []
    for key, value in snapshot.items():
        title = _TITLES.get(key, key)
        body = value[:per_item]
        parts.append(f"### {title}（{key}）\n{body}")
    return "\n\n".join(parts)


class SnapshotCache:
    """后台定时轮询的快照缓存。

    get() 返回 (快照, 距采集的秒数)；缓存为空时（服务刚启动）触发一次
    即时采集兜底。collect_snapshot 自身逐项降级不抛错，轮询循环再兜一层
    防止意外异常杀死后台任务。
    """

    def __init__(self, interval: float = 30):
        self._interval = interval
        self._snapshot: dict[str, str] | None = None
        self._collected_at: float = 0.0
        self._task: asyncio.Task | None = None
        self._refresh_lock = asyncio.Lock()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self.refresh()
            except Exception:
                pass  # 单轮失败不退出，下一轮重试
            await asyncio.sleep(self._interval)

    async def refresh(self) -> None:
        async with self._refresh_lock:
            self._snapshot = await collect_snapshot()
            self._collected_at = time.monotonic()

    async def get(self) -> tuple[dict[str, str], float]:
        if self._snapshot is None:
            await self.refresh()
        return self._snapshot, time.monotonic() - self._collected_at
