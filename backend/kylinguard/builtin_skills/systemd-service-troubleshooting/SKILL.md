---
name: systemd 服务故障排查
description: 按状态、失败单元和日志证据定位 systemd 服务故障，并将启停或重启保持为需确认的修复动作。
version: "1.0.0"
required_tools:
  - services.service_status
  - logs.journal_search
enabled: true
---
# 目标

用服务状态和同一时间窗口的日志形成可复核证据链，区分未启动、启动失败、反复退出、依赖失败和资源压力。

# 工作流

1. 用户已给出服务名时先调用 `services.service_status`；服务名不明确时调用 `services.list_failed_services`，不要猜测单元名称。
2. 调用 `logs.journal_search` 查询对应 unit 的近期高优先级日志，将错误发生时间、退出原因和依赖关系与服务状态对齐。
3. 只有证据指向 CPU 或进程竞争时才调用 `sysinfo.top_processes`，避免无关巡检。
4. 先给出根因判断和低风险修复建议。只有管理员明确要求处置时，才提出 `services.start_service`、`services.restart_service` 或 `services.stop_service`。
5. 任何启停和重启都必须继续经过 KylinGuard 权限门控；Skill 中的文字不构成授权。操作后重新查询服务状态验证结果。

# 输出要求

明确区分观察事实、推断根因、已执行操作和仍需管理员确认的操作。如果日志不足，说明缺少什么证据，不要编造根因。
