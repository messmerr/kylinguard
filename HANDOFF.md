# 麒盾 KylinGuard — 项目交接文档

> 面向队友与无上下文的 AI 助手。读完这份文档应能理解项目在做什么、
> 现在到哪一步、代码怎么组织、以及下一步该做什么。
> 最后更新：2026-07-14。

## 1. 项目一句话

第十五届"中国软件杯"信创专项 **A2 赛题**参赛作品（出题企业：麒麟软件）：
一个部署在麒麟服务器上的**安全智能运维 Agent**。管理员用自然语言下达运维指令，
Agent 感知系统环境、规划执行计划、按会话权限授权完整能力并全程留痕。
赛题核心命题是**驯服大模型推理的不可控性**——把能力强但不可全信的 LLM 安全地放进生产机房。

- **初赛提交截止：2026-07-20**，目标冲击国赛获奖。
- 评分：功能完整性 55% / 创新与实用性 25% / 文档与演示 20%。
- 团队：3 名 CS 本科生，以 AI 编码工具为主力开发。
- 仓库：`messmerr/kylinguard`（private）。

## 2. 当前状态（2026-07-14）

**M1 + M2 + 会话权限系统已完成，正在收口部署迁移与交付材料。**

- 后端当前收集 575 个 pytest 用例；本轮 Skill/MCP/流水线等 140 项定向回归全绿。
  macOS 全量结果为 554 通过、21 个 Linux 平台相关失败（19 个文件插件与 1 个
  MCP 集成用例依赖 `renameat2`，另 1 个是 `/etc` 在 macOS 解析为 `/private/etc`）；
  数量以后续麒麟/Linux 环境的实际输出为准。
- 前端当前 76 个 Node 状态测试与生产构建通过，七个视图可用；数量以后续实际测试输出为准。
- 当前扩展增强工作位于 `codex/custom-extensions`，尚未合并 `main`。

能演示的完整闭环：打开工作台 → 对话下发指令 → 实时看到感知/规划(流式)/三道闸校验/执行
各阶段 → 按会话权限自动执行或弹授权卡 → 审计回放中心按时间线回看整条哈希链
并校验完整性 → 权限与安全页管理可信目录/有效授权/高级规则 → 仪表盘看全局统计。

扩展页现可管理自定义 stdio MCP 与声明式 Skill。MCP 配置保存、测试、显式启停
和运行时热加载相互分离，敏感环境变量只写并独立落盘；Skill 按轮冻结版本/哈希，
默认由模型渐进匹配，也可作为正文内的多个 `@` 标签显式组合。Skill 只提供工作流，
可选工具依赖只检查 MCP 是否就绪；它既不限制工具，也不能绕过既有风险、权限与
审计边界。常见 `mcpServers` JSON、
单项配置或 stdio 命令可以解析为停用草稿；单个 `SKILL.md` 也可规范化导入，第三方
专用运行字段与未加载的配套资源会明确提示并忽略，不会被当成本地授权。
Docker 基础镜像不内置 Node/npm；Node MCP 应在派生镜像中锁定版本安装，或以
容器架构兼容的完整运行包只读挂载。扩展页命令必须是容器内绝对路径，MCP 的
`cwd` 默认是入口父目录，也可显式选择容器内规范化绝对目录；read_only 部署中的 npm 临时缓存只能显式放到 `/tmp`。

## 3. 技术栈与运行

- **后端**：Python 3.10+ / FastAPI / openai SDK / 官方 mcp SDK(FastMCP, stdio) /
  pydantic v2 / stdlib sqlite3。**自研 Agent 核心，不用 LangChain。**
- **前端**：Vue3 + Element Plus + markdown-it + highlight.js，Vite 构建，后端静态托管。
- **存储**：单个 SQLite 文件（WAL）保存审计链、会话、策略与模型元数据；
  模型 API Key 单独保存在工作区外的受限文件中，SQLite 只存随机引用。
- **LLM**：GUI 管理 OpenAI / DeepSeek / DashScope / OpenAI 兼容提供商；
  规划与审查独立配置，会话可固定主模型与推理强度。模型管理无需登录并写审计；
  模型发现为 OpenAI 兼容接口提供
  `low` / `medium` / `high` 默认档位，
  并允许批量或逐模型覆盖。完全访问和高风险执行仍必须明确确认。

运行方式见 [README.md](README.md)。要点：模型提供商、Key、可用模型和默认值全部在
“模型服务”中配置，不存在环境变量回退。Windows 上部分 Linux 命令降级，
联调请用 WSL 或 Linux。

## 4. 架构地图

### 五阶段安全流水线（一次请求的生命周期）

```
感知(Perceive) → 规划(Plan) → 校验(Verify) → 执行(Execute) → 溯源(Trace)
  快照缓存      流式JSON计划   风险+权限      MCP插件+OS身份       哈希链审计
```

### 三道闸（校验阶段的核心，赛题命题的答案）

1. **规则引擎**（`rules.py`，静态）：证明简单只读命令可以自动执行，并把
   破坏性模式、系统配置、完整 shell、提权与载荷执行器分类为需显式权限；
   命令类别本身不再成为不可覆盖的能力禁令。
2. **独立 LLM 审查员**（`reviewer.py`）：与规划模型完全隔离的第二个 LLM，
   只看"命令+原始意图+环境摘要"，判断是否安全、是否符合意图（抗提示词注入关键层）。
   任何失败收敛为最高风险告警；Reviewer 提升确认强度，不替用户作最终授权。
3. **风险与权限门控**（`gate.py` + `authorization.py`）：空输入、NUL、非法 argv
   等执行协议错误不可覆盖；其余操作结合风险、会话模式、可信目录、一次/会话
   授权决定自动、询问或拒绝。完全访问按定义覆盖产品层路径与风险策略。
   高风险批准只能按当前动作单次授权并写入审计。

### 四种会话权限

`read_only`（只读）/ `ask`（确认后执行，默认）/
`trusted_workspace`（可信目录内自动创建和修改）/ `full_access`（执行账户权限内
启用完整 shell、文件、网络与进程能力且不逐项确认）。后两者有 TTL；完全访问
默认可用、需要显式开启但不会自动获得 root，且后端重启后
总会收回。`KG_EXEC_USER` 可选；留空使用后端当前 OS 身份。不同 UID 仅代表
执行账户分离，控制面是否真正隔离仍由文件权限/ACL 决定。

每个会话还持久化独立 `workspace_root`：新任务可先选择后端/WSL 可见目录，
终端调用默认注入该 `cwd`，规划上下文也会明确告知模型。工作目录是项目上下文，
不与可信目录授权或 OS 访问边界混为一谈。

### 后端模块（`backend/kylinguard/`）

| 文件 | 职责 |
|------|------|
| `config.py` | 全局配置（pydantic-settings，`KG_` 前缀，读项目根 .env） |
| `models.py` | 共享数据模型（RiskLevel/PlanStep/各类判定/ExecResult） |
| `audit.py` | 哈希链审计日志（SQLite WAL + SHA-256 链，防篡改；写失败即致命） |
| `sessions.py` | 会话元数据、权限持久化、授权执行租约与增量表迁移 |
| `permissions.py` | 会话权限上下文、待决授权、TTL 与版本控制 |
| `authorization.py` | 工具调用能力描述、可信目录与权限模式裁决 |
| `sanitization.py` | 进入 LLM/SSE/审计前的敏感信息与正文摘要处理 |
| `storage_security.py` | 以 0600/0700 准备控制面数据库与状态目录 |
| `policy.py` | 自定义策略库（黑名单/白名单/保护路径，与内置规则合并判定） |
| `rules.py` | 规则引擎（三道闸第一道）+ 内置规则导出 |
| `reviewer.py` | 独立 LLM 审查员（三道闸第二道） |
| `gate.py` | 风险分级门控（三道闸第三道） |
| `executor.py` | 完整 shell + 精确 argv 执行（cwd/超时/stdin 隔离/进程组清理/有界双流输出） |
| `llm.py` | LLM 网关（OpenAI 兼容 + 指数退避 + 流式接口 + 双实例） |
| `llm_config.py` | 提供商/模型/默认值/会话路由、独立 Key 文件与每轮运行快照 |
| `mcp_config.py` | 自定义 stdio MCP 配置、只写秘密环境变量与工具发现状态 |
| `skills.py` | 内置/自定义 Skill 加载、渐进披露目录、启停状态、每轮快照与依赖检查 |
| `context_files.py` | `@文件` 候选检索与工作目录内相对路径校验（不读取正文） |
| `snapshot.py` | 系统快照采集（并发）+ 后台轮询缓存（SnapshotCache） |
| `registry.py` | 工具元数据注册表（风险/提权声明；未注册按最高危） |
| `planner.py` | 规划器（流式"markdown 分析 + JSON 决策块"协议） |
| `mcp_client.py` | MCP stdio 客户端管理器（内置插件、自定义服务热加载与统一调用） |
| `pipeline.py` | 五阶段流水线编排 + 多轮上下文 + 人工确认 + 阶段事件 |
| `api.py` | FastAPI 入口（SSE 对话、会话/策略/统计端点、静态托管） |
| `plugins/` | 7 个领域插件：sysinfo/services/logs/network/disk/security/files + run_command |

### 前端结构（`frontend/src/`）

- `App.vue`：工作区导航壳（七个工作区视图切换）。
- `views/`：`ChatView`（对话运维）/ `ModelSettingsView`（模型服务）/
  `ExtensionsView`（MCP 与 Skill 扩展）/
  `AuditView`（审计回放）/ `PolicyView`（权限与安全）/
  `DashboardView`（总览）/ `AlertsView`（告警）。
- `components/`：`Sidebar`（会话历史）/ `StatusPanel`（右侧实时状态）/
  `ModelSelector`（会话模型与推理强度）/ `TraceStep`（步骤行）/
  `ConfirmCard`（确认卡）/ `MarkdownText` / `InlineMentionEditor`（正文内
  Skill 与服务器文件标签编辑器）。
- `composables/`：`useChat.js`（SSE 事件聚合成回合制渲染模型）/
  `useModels.js`（提供商、默认值与会话模型状态）/
  `useExtensions.js`（MCP/Skill 管理）/ `useComposerMentions.js`（正文内 `@`
  Skill 与服务器文件候选、检索和结构化引用）/
  `usePermissions.js`（权限模式/可信目录/授权）/ `useApi.js`（请求封装）。

### SSE 事件协议（`useChat.js` 消费的事件类型）

`session_created` / `user_query` / `snapshot` / `phase`(纯UI阶段指示) /
`progress`(连接、重试、工具参数生成等瞬时进度) /
`assistant_delta`(流式思考增量) / `plan` / `verification` / `confirm_request` /
`confirm_result` / `permission_context` / `permission_request` /
`permission_result` / `step_rewrite` / `capability_error` / `execution` /
`execution_authorized` / `execution_authorization_failed` / `task_error` /
`skill_routing_catalog` / `skill_routing_decision` / `skill_selected` /
`skill_not_selected` / `final_answer` / `fatal` / `done`。
其中 `phase`、`progress` 和 `assistant_delta` 只走 UI 不入审计链；其余整段落审计。
规划器进入隐藏 JSON 决策块后，`progress` 只发送活动类型与生成字符/字节数，
不会发送文件正文、路径值或原始工具参数。
同一步骤的 verification/confirm/execution 事件用 `step_id` 关联。

## 5. 关键设计决策与原因（改动前先理解）

- **不用 LangChain**：规避 LoongArch 依赖风险与"拼装货"质疑，核心自研千行级。
- **密码用 PBKDF2 而非 bcrypt**：bcrypt 是 C 扩展，LoongArch pip 源可能没轮子；
  PBKDF2 是 stdlib，零依赖。
- **规则引擎 argv 级匹配**：源自对 Codex CLI / Claude Code 权限模型的调研——
  字符串通配符匹配脆弱易绕过，argv 级 + 命令+参数级白名单更稳。
- **双 LLM 交叉校验**：审查员系统提示词独立、不看规划推理过程、不接收工具输出原文
  （Dual-LLM 红线，避免把注入面引入第二道闸）。
- **哈希链审计**：每条事件带前序哈希；权限状态、grant 消费和对应安全事件
  同事务提交，**审计写入失败视为致命并回滚**（不允许"干了但没记录"）。
- **正交分层**：门控决定"何时问人"（审批时机），执行器决定"技术上能做什么"
  （能力边界），互为纵深防御。
- **硬拒绝与权限请求分离**：无效工具参数/协议错误 hard deny；块设备、根目录
  破坏、控制面/敏感路径属于显著高风险，普通模式要求二次确认或拒绝，用户
  显式开启的完全访问则不伪装成能力沙箱。
- **文件能力结构化**：写文档不再靠 `echo >`/`tee`；原子文件工具支持
  expected SHA-256、基于 dirfd 的竞态封闭和不回显正文的变更摘要。
- **Skill 渐进披露**：正常规划提示只携带已启用 Skill 的名称和说明；模型明确
  选择后才加载并冻结正文。没有独立的前置路由模型，普通任务只发起正常规划请求。
- **任何不确定收敛到"不执行"**：JSON 解析失败、LLM 失败、审查失败——全部 fail closed。

## 6. 里程碑进度

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| M1 | 骨架 + 五阶段流水线 + 3 插件 + run_command + 最小对话页，端到端打通 | ✅ 完成 |
| M2 | 6 插件 + 审计回放 + 策略管理 + 仪表盘 + 多轮/流式对话 | ✅ 完成 |
| M2.5 | 四档权限 + 结构化文件工具 + 可撤销授权 + 等待/错误可观测 | ✅ 完成 |
| M3 | install.sh + systemd + sudoers/非 root 容器已完成；LoongArch 实机与性能待验 | 🟨 部分完成 |
| M4 | 60 例安全基准已完成；5 份文档、PPT、≤7 分钟演示视频待完成 | 🟨 部分完成 |

## 7. 未完成清单（下一步工作）

### M3（**阻塞于外部依赖：LoongArch 虚拟机镜像尚未批下来**，通过答疑 QQ 群申请）

- [x] `install.sh`：依赖从 pip 源在线安装（赛题要求第三方依赖不打进安装包）。
- [x] systemd 服务单元、资源上限与只读文件系统边界。
- [x] 创建 `kylinguard-exec` 专用执行账户；普通能力只以该非特权账户运行，
      root 操作只进入 root-owned 参数校验 helper。
- [x] **支持控制面/执行面 OS 隔离**：配置独立 `KG_EXEC_USER` 时审计数据库与
  密钥对执行账户不可访问；本地同身份模式只提供显式路径拦截并在 UI 中告警。
- [ ] 在 LoongArch 麒麟 V11 实机验证依赖安装与端到端运行；性能实测
      （并发会话、快照耗时、端到端延迟）。

### M4

- [x] 安全对抗基准：60 条用例，覆盖提示注入、危险命令变体、关键配置可靠性，
      当前 60/60 通过且安全操作误拦截率为 0。
- [ ] 5 份文档：需求分析、功能设计、产品说明书、功能测试报告、性能测试报告。
- [ ] PPT + ≤7 分钟演示视频（主线：对话运维走查 → 审计回放 → 提示词注入被拦截对抗演示）。

## 8. 已知技术债（不阻塞功能，但要知道）

- 前端刷新会丢失进行中任务的 SSE 流（无断线重连）。
- 多轮 conversation 无长度压缩，超长会话可能撑爆上下文窗口。
- LoongArch 上 MCP stdio 子进程行为尚未实机验证（x86/WSL 已验证正常）。

## 9. 项目约定（重要，务必遵守）

- **git 提交信息不加 `Co-Authored-By` 等 AI 署名尾行**（作品以团队名义提交）。
- **过程性文档放 `docs/superpowers/`，已在 .gitignore，不入库**；只有长期有价值的
  文档（本文件、README、将来的设计/测试文档）进仓库。
- `.env` 已 gitignore，**绝不入库、绝不外传密钥**；配置项统一 `KG_` 前缀。
- 所有面向用户的文案、注释、提交说明用中文；代码标识符用英文。
- 排期按里程碑驱动而非按天分配。

## 10. 给无上下文 AI 助手的提示

- 先读本文件 + `README.md` + `backend/kylinguard/pipeline.py`（流水线主干）
  即可建立全局认知。
- 改后端务必跑 `cd backend && pytest`；改规则/门控/审查的安全逻辑时，
  测试用例是基准，不许为迁就实现反向放宽测试。
- 安全相关改动必须保持“能力与授权分离”：协议/控制面 fail closed，普通命令
  风险由权限模式决定，不能重新用静态黑名单阉割 Agent 通用能力。
- 联调需要真实 systemctl/journalctl 时用 WSL 或 Linux，不要在 Windows 上判断
  Linux 命令行为。
