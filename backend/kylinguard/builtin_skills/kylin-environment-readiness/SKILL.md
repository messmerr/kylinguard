---
name: 银河麒麟赛题环境核验
description: 面向部署前检查、赛场环境确认和兼容性报告，使用版本、版本文件、架构、内核、systemd、Python、Node 与诊断组件证据判断当前主机是否匹配银河麒麟高级服务器版 V11 + LoongArch64；只报告已验证事实，不把“命令存在”夸大为“功能可用”。
version: "1.0.0"
required_tools:
  - kylin.system_identity
  - kylin.capability_matrix
  - kylin.deployment_readiness
enabled: true
---
# 适用范围

用于回答“这台机器是不是比赛要求的麒麟环境”“能否在这里从源码构建”“还缺什么依赖”“哪些麒麟原生工具可用”等问题。它是环境核验，不负责安装软件、修改仓库源、升级系统或启停服务。

# 强制原则

1. 环境结论必须同时引用操作系统、版本、版本类型和 CPU 架构证据，不能只凭界面外观、主机名或 `/etc/kylin-release` 单一文件下结论。
2. `contest_target.status=matched` 才能写“赛题目标环境完全匹配”；`partial` 只能写“部分证据待确认”；`mismatch` 必须列出不匹配项。
3. `capability_matrix` 中 `capability_detected` 只证明找到了可执行文件，不证明已验证其参数、权限或输出契约。除 `nkvers` 身份查询外，不得把探测结果写成“已完成原生诊断”。
4. `deployment_readiness.ready=false` 时不得写“可以部署”。必须逐条列出 blocker，并把 warning 与 blocker 分开。
5. 缺失可选命令不等于系统不可用。只有就绪度工具标出的 blocker 才能作为阻断结论。
6. 本 Skill 全程只读。用户即使说“顺便装一下”，也要先完成报告，再把安装作为独立变更任务交给正常权限、确认和审计流程。

# 工作流

## 1. 建立系统身份

首先调用 `kylin.system_identity`，记录：

- `kylin.detected`、`kylin.version`、`kylin.edition`；
- `architecture.raw` 与 `architecture.normalized`；
- `kernel.release`、`runtime.init_system`、`runtime.glibc`；
- `contest_target` 四个判定项及总状态；
- `evidence_sources` 和 `warnings`。

如果 `kylin.detected=false`，不要继续把后续通用 Linux 工具描述成麒麟原生能力；仍可完成依赖核验，但报告标题应写“非目标环境兼容检查”。

如果版本或服务器版身份为 `null/unknown`，明确指出缺少哪一类证据。不要用内核版本、桌面主题或年份猜测产品版本。

## 2. 验证源码构建与运行条件

调用 `kylin.deployment_readiness`。逐项核对：

1. Python 是否达到 3.10；
2. Node.js 是否达到 18，npm 是否存在；
3. systemctl、journalctl、ps、free、df、ss、find、stat 等关键命令是否存在；
4. 阻断项与降级警告是否为空；
5. `ready` 与列出的 blocker 是否一致。

Node/npm 在本项目中用于目标机源码构建前端；赛事不允许携带已构建前端时，它们是部署条件，不要把“后端当前能启动”误写成“完整源码部署就绪”。

## 3. 建立能力矩阵

调用 `kylin.capability_matrix`，按两层报告：

- 麒麟原生能力：列出已探测和未探测的组件、候选命令及探测状态；
- 通用降级能力：列出 systemd、iproute2、I/O 和 ELF 检查链是否完整。

只有工具状态为 `identity_verified` 时，才能说明该原生命令已经被当前实现实际调用验证。其他已探测工具应写“已发现，调用契约未由本轮验证”。

## 4. 形成确定性结论

使用下列分级，不自行发明百分制：

- **完全匹配**：`contest_target.status=matched` 且 `deployment_readiness.ready=true`；
- **目标环境匹配但部署受阻**：身份完全匹配，但存在构建或关键命令 blocker；
- **部分确认**：身份为 `partial`，列出待补证据；
- **环境不匹配**：身份为 `mismatch`，列出明确的 false 项；
- **无法核验**：关键采集均失败，此时只能报告证据缺口。

不得用“原生工具数量多”覆盖版本、架构或构建依赖的阻断项。

# 停止条件

- 身份工具返回结构不完整或采集失败：停止“匹配”判断，保留已取得证据；
- 架构不是 LoongArch64：不再声称满足赛题指定平台，但可以继续列出开发机兼容情况；
- Node、npm 或关键命令缺失：完成缺口清单后停止，不尝试安装；
- 版本证据相互冲突：同时呈现冲突来源，结论降为“无法核验”，不得任选一个值。

# 输出格式

1. **核验结论**：完全匹配、受阻、部分确认、不匹配或无法核验；
2. **身份与架构证据**：系统、版本类型、版本、架构、内核、systemd；
3. **构建与运行条件**：Python、Node、npm、关键命令；
4. **麒麟能力矩阵**：原生组件与通用降级链分开；
5. **阻断项**：只列 blocker；
6. **警告与证据缺口**：不影响运行但影响诊断深度或结论置信度的项目；
7. **下一步建议**：只给出建议，不声称已安装或修复。
