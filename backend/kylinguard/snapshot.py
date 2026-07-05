"""感知阶段：主动采集系统快照注入 LLM 上下文，减少幻觉。

全部为只读命令；逐项独立采集，单项失败降级为错误说明，绝不抛出。
SnapshotCache 后台定时刷新，请求路径零采集延迟；该缓存同时是
仪表盘（M2）的数据源。AlertStore 在每轮快照后检测阈值，向前端
推送主动告警（磁盘/内存/CPU/失败服务），同一告警 10 分钟内不重复。
"""
import asyncio
import re
import sys
import time
import uuid

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


def _parse_disk_pcts(snapshot: dict[str, str]) -> list[tuple[str, int]]:
    """解析磁盘快照，返回 [(分区名, 使用率%)] 列表，解析失败则跳过。"""
    raw = snapshot.get("disk", "")
    results = []
    if _IS_WINDOWS:
        # Windows: "C    total=449.4G used=439.3G free=10.1G"
        for line in raw.splitlines():
            m = re.search(r'(\w+)\s+total=([\d.]+)G\s+used=([\d.]+)G', line)
            if m:
                total, used = float(m.group(2)), float(m.group(3))
                if total > 0:
                    results.append((m.group(1) + "盘", int(used / total * 100)))
    else:
        # Linux: "df -h" 输出，最后一列为使用率%，第6列为挂载点
        for line in raw.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 6:
                pct_str = parts[4].rstrip('%')
                if pct_str.isdigit():
                    results.append((parts[5], int(pct_str)))
    return results


def _parse_memory_pct(snapshot: dict[str, str]) -> int | None:
    """解析内存快照，返回使用率百分比，解析失败返回 None。"""
    raw = snapshot.get("memory", "")
    if _IS_WINDOWS:
        m = re.search(r'total=(\d+)MB used=(\d+)MB', raw)
        if m and int(m.group(1)) > 0:
            return int(int(m.group(2)) / int(m.group(1)) * 100)
    else:
        m = re.search(r'Mem:\s+(\d+)\s+(\d+)', raw)
        if m and int(m.group(1)) > 0:
            return int(int(m.group(2)) / int(m.group(1)) * 100)
    return None


def _parse_cpu_pct(snapshot: dict[str, str]) -> int | None:
    """解析 CPU 负载，返回百分比，解析失败返回 None。"""
    raw = snapshot.get("uptime_load", "")
    if _IS_WINDOWS:
        m = re.search(r'CPU:\s*(\d+)%', raw)
        if m:
            return int(m.group(1))
    else:
        m = re.search(r'load average[s]?:\s*([\d.]+)', raw)
        if m:
            # 简单用 load 值估算（单核满载=1.0，多核类推）
            return min(int(float(m.group(1)) * 100), 100)
    return None


def _has_failed_units(snapshot: dict[str, str]) -> bool:
    raw = snapshot.get("failed_units", "")
    if raw.startswith("[采集失败]"):
        return False
    if _IS_WINDOWS:
        lines = [l for l in raw.splitlines() if l.strip()
                 and not l.startswith("Name") and not l.startswith("----")
                 and "Stopped" in l]
        return len(lines) > 0
    return "0 loaded units" not in raw and len(raw.strip().splitlines()) > 1


def detect_anomalies(snapshot: dict[str, str]) -> list[dict]:
    """检测快照中的异常，返回告警列表（severity: warning|critical）。"""
    alerts = []

    for name, pct in _parse_disk_pcts(snapshot):
        if pct >= 95:
            alerts.append({"kind": f"disk_{name}", "severity": "critical",
                           "title": f"磁盘空间严重不足",
                           "message": f"{name} 使用率已达 {pct}%，剩余空间极少，请立即清理",
                           "metric": f"{pct}%"})
        elif pct >= 90:
            alerts.append({"kind": f"disk_{name}", "severity": "warning",
                           "title": f"磁盘空间紧张",
                           "message": f"{name} 使用率达 {pct}%，建议清理不必要文件",
                           "metric": f"{pct}%"})

    mem = _parse_memory_pct(snapshot)
    if mem is not None:
        if mem >= 95:
            alerts.append({"kind": "memory", "severity": "critical",
                           "title": "内存严重不足",
                           "message": f"内存使用率 {mem}%，系统可能出现 OOM",
                           "metric": f"{mem}%"})
        elif mem >= 85:
            alerts.append({"kind": "memory", "severity": "warning",
                           "title": "内存使用率偏高",
                           "message": f"内存使用率 {mem}%，建议排查高内存进程",
                           "metric": f"{mem}%"})

    cpu = _parse_cpu_pct(snapshot)
    if cpu is not None and cpu >= 80:
        alerts.append({"kind": "cpu", "severity": "warning",
                       "title": "CPU 负载偏高",
                       "message": f"CPU 使用率 {cpu}%，建议排查高 CPU 进程",
                       "metric": f"{cpu}%"})

    if _has_failed_units(snapshot):
        alerts.append({"kind": "failed_units", "severity": "warning",
                       "title": "存在异常停止的服务",
                       "message": "有配置为自动启动但已停止的服务，请检查服务状态",
                       "metric": "!"})

    return alerts


class AlertStore:
    """内存告警存储，带 10 分钟同类去重冷却。"""

    COOLDOWN = 600  # 同一 kind 告警冷却秒数

    def __init__(self):
        self._alerts: dict[str, dict] = {}   # alert_id → alert
        self._last_fired: dict[str, float] = {}  # kind → timestamp

    def ingest(self, raw_alerts: list[dict]) -> list[dict]:
        """注入新告警（去重冷却），返回本轮新增的告警列表。"""
        now = time.time()
        new = []
        for a in raw_alerts:
            kind = a["kind"]
            last = self._last_fired.get(kind, 0)
            if now - last < self.COOLDOWN:
                continue
            alert_id = uuid.uuid4().hex[:12]
            record = {**a, "id": alert_id, "ts": int(now), "acked": False}
            self._alerts[alert_id] = record
            self._last_fired[kind] = now
            new.append(record)
        return new

    def active(self) -> list[dict]:
        """返回所有未确认的告警（按时间降序）。"""
        return sorted(
            [a for a in self._alerts.values() if not a["acked"]],
            key=lambda a: a["ts"], reverse=True,
        )

    def ack(self, alert_id: str) -> bool:
        a = self._alerts.get(alert_id)
        if a is None:
            return False
        a["acked"] = True
        return True


class SnapshotCache:
    """后台定时轮询的快照缓存。

    get() 返回 (快照, 距采集的秒数)；缓存为空时（服务刚启动）触发一次
    即时采集兜底。collect_snapshot 自身逐项降级不抛错，轮询循环再兜一层
    防止意外异常杀死后台任务。每轮刷新后自动检测异常并注入 AlertStore。
    """

    def __init__(self, interval: float = 30):
        self._interval = interval
        self._snapshot: dict[str, str] | None = None
        self._collected_at: float = 0.0
        self._task: asyncio.Task | None = None
        self._refresh_lock = asyncio.Lock()
        self._alert_store = AlertStore()

    @property
    def alert_store(self) -> AlertStore:
        return self._alert_store

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
            self._alert_store.ingest(detect_anomalies(self._snapshot))

    async def get(self) -> tuple[dict[str, str], float]:
        if self._snapshot is None:
            await self.refresh()
        return self._snapshot, time.monotonic() - self._collected_at
