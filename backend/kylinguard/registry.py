"""工具元数据注册表：风险等级/是否提权集中声明，校验器直接消费。

安全模型的一部分：未注册的（第三方）工具一律按最高危处理。
"""
from kylinguard.models import RiskLevel, ToolMeta


def _meta(server: str, tool: str, risk: RiskLevel, *, needs_sudo: bool = False,
          dynamic: bool = False, description: str = "") -> tuple[str, ToolMeta]:
    return f"{server}.{tool}", ToolMeta(
        server=server, tool=tool, risk=risk, needs_sudo=needs_sudo,
        dynamic=dynamic, description=description,
    )


TOOL_REGISTRY: dict[str, ToolMeta] = dict([
    # 系统观测（全只读）
    _meta("sysinfo", "system_snapshot", RiskLevel.LOW, description="采集系统整体快照"),
    _meta("sysinfo", "top_processes", RiskLevel.LOW, description="资源占用最高的进程"),
    _meta("sysinfo", "disk_usage", RiskLevel.LOW, description="磁盘分区使用情况"),
    # 进程与服务
    _meta("services", "service_status", RiskLevel.LOW, description="查询服务状态"),
    _meta("services", "list_failed_services", RiskLevel.LOW, description="列出失败的服务"),
    _meta("services", "start_service", RiskLevel.MEDIUM, needs_sudo=True, description="启动服务"),
    _meta("services", "restart_service", RiskLevel.MEDIUM, needs_sudo=True, description="重启服务"),
    _meta("services", "stop_service", RiskLevel.HIGH, needs_sudo=True, description="停止服务"),
    # 日志分析（只读）
    _meta("logs", "journal_search", RiskLevel.LOW, description="检索 systemd 日志"),
    _meta("logs", "tail_file", RiskLevel.LOW, description="查看 /var/log 下日志尾部"),
    # 通用命令：风险随命令内容动态判定；未命中只读白名单时至少 MEDIUM
    _meta("run_command", "run_command", RiskLevel.MEDIUM, dynamic=True,
          description="执行自由 shell 命令（经三道闸校验）"),
])


def get_meta(server: str, tool: str) -> ToolMeta:
    key = f"{server}.{tool}"
    if key in TOOL_REGISTRY:
        return TOOL_REGISTRY[key]
    return ToolMeta(server=server, tool=tool, risk=RiskLevel.HIGH,
                    description="未注册工具，按最高危处理")
