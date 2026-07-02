# 麒盾 KylinGuard — 项目交接文档

> 面向队友与无上下文的 AI 助手。读完这份文档应能理解项目在做什么、
> 现在到哪一步、代码怎么组织、以及下一步该做什么。
> 最后更新：2026-07-02。

## 1. 项目一句话

第十五届"中国软件杯"信创专项 **A2 赛题**参赛作品（出题企业：麒麟软件）：
一个部署在麒麟服务器上的**安全智能运维 Agent**。管理员用自然语言下达运维指令，
Agent 感知系统环境、规划执行计划、多重校验安全性、以最小权限执行并全程留痕。
赛题核心命题是**驯服大模型推理的不可控性**——把能力强但不可全信的 LLM 安全地放进生产机房。

- **初赛提交截止：2026-07-20**，目标冲击国赛获奖。
- 评分：功能完整性 55% / 创新与实用性 25% / 文档与演示 20%。
- 团队：3 名 CS 本科生，以 AI 编码工具为主力开发。
- 仓库：`messmerr/kylinguard`（private）。

## 2. 当前状态（2026-07-02）

**M1 + M2 全部完成——功能层面已齐，剩下的是部署迁移与交付材料。**

- 后端约 180 个 pytest 用例（134 个测试函数，含参数化展开）全绿。
- 前端构建通过，四个视图均已实机走查。
- 全部提交在 `feat/m1-pipeline` 分支，已合并进 `main`。

能演示的完整闭环：登录 → 对话下发指令 → 实时看到感知/规划(流式)/三道闸校验/执行
各阶段 → 中高危弹确认卡 → 审计回放中心按时间线回看整条哈希链并校验完整性 →
策略管理增删规则 → 仪表盘看全局安全统计。

## 3. 技术栈与运行

- **后端**：Python 3.10+ / FastAPI / openai SDK / 官方 mcp SDK(FastMCP, stdio) /
  pydantic v2 / stdlib sqlite3。**自研 Agent 核心，不用 LangChain。**
- **前端**：Vue3 + Element Plus + markdown-it + highlight.js，Vite 构建，后端静态托管。
- **存储**：单个 SQLite 文件（WAL），含审计链、会话、用户、策略四类表。
- **LLM**：OpenAI 兼容 API，默认 DeepSeek，规划与审查用两个独立实例。

运行方式见 [README.md](README.md)。要点：`.env` 需配 `KG_LLM_API_KEY` 与
`KG_ADMIN_PASSWORD`；Windows 上部分 Linux 命令降级，联调请用 WSL 或 Linux。

## 4. 架构地图

### 五阶段安全流水线（一次请求的生命周期）

```
感知(Perceive) → 规划(Plan) → 校验(Verify) → 受限执行(Execute) → 溯源(Trace)
  快照缓存      流式JSON计划   三道闸         MCP插件+降权          哈希链审计
```

### 三道闸（校验阶段的核心，赛题命题的答案）

1. **规则引擎**（`rules.py`，静态）：argv 级匹配（非字符串正则），黑名单、
   保护路径写操作、shell 元字符、命令+参数级只读白名单、提权/载荷执行器防御，
   无法解析一律 fail closed。
2. **独立 LLM 审查员**（`reviewer.py`）：与规划模型完全隔离的第二个 LLM，
   只看"命令+原始意图+环境摘要"，判断是否安全、是否符合意图（抗提示词注入关键层）。
   任何失败收敛到"最不安全"。
3. **风险分级门控**（`gate.py`）：综合三方判定，拒绝一票否决、风险取最严；
   低危自动放行 / 中危一键确认 / 高危二次确认。

### 后端模块（`backend/kylinguard/`）

| 文件 | 职责 |
|------|------|
| `config.py` | 全局配置（pydantic-settings，`KG_` 前缀，读项目根 .env） |
| `models.py` | 共享数据模型（RiskLevel/PlanStep/各类判定/ExecResult） |
| `audit.py` | 哈希链审计日志（SQLite WAL + SHA-256 链，防篡改；写失败即致命） |
| `auth.py` | 登录鉴权（PBKDF2 密码哈希 + 内存 token） |
| `sessions.py` | 会话元数据（侧栏历史列表数据层） |
| `policy.py` | 自定义策略库（黑名单/白名单/保护路径，与内置规则合并判定） |
| `rules.py` | 规则引擎（三道闸第一道）+ 内置规则导出 |
| `reviewer.py` | 独立 LLM 审查员（三道闸第二道） |
| `gate.py` | 风险分级门控（三道闸第三道） |
| `executor.py` | 受限命令执行（无 shell、超时、截断、sudo -u 降权） |
| `llm.py` | LLM 网关（OpenAI 兼容 + 指数退避 + 流式接口 + 双实例） |
| `snapshot.py` | 系统快照采集（并发）+ 后台轮询缓存（SnapshotCache） |
| `registry.py` | 工具元数据注册表（风险/提权声明；未注册按最高危） |
| `planner.py` | 规划器（流式"markdown 分析 + JSON 决策块"协议） |
| `mcp_client.py` | MCP stdio 客户端管理器（拉起插件子进程、统一调用） |
| `pipeline.py` | 五阶段流水线编排 + 多轮上下文 + 人工确认 + 阶段事件 |
| `api.py` | FastAPI 入口（SSE 对话、鉴权、会话/策略/统计端点、静态托管） |
| `plugins/` | 6 个 MCP 插件：sysinfo/services/logs/network/disk/security + run_command |

### 前端结构（`frontend/src/`）

- `App.vue`：登录门 + 顶栏导航壳（四视图切换）。
- `views/`：`LoginView` / `ChatView`（对话运维）/ `AuditView`（审计回放）/
  `PolicyView`（策略管理）/ `DashboardView`（仪表盘）。
- `components/`：`Sidebar`（会话历史）/ `StatusPanel`（右侧实时状态）/
  `TraceStep`（步骤行）/ `ConfirmCard`（确认卡）/ `MarkdownText`。
- `composables/`：`useChat.js`（SSE 事件聚合成回合制渲染模型，核心状态层）/
  `useAuth.js`（token 持久化 + 401 自动登出 + apiFetch 封装）。

### SSE 事件协议（`useChat.js` 消费的事件类型）

`session_created` / `user_query` / `snapshot` / `phase`(纯UI阶段指示) /
`assistant_delta`(流式思考增量) / `plan` / `verification` / `confirm_request` /
`confirm_result` / `execution` / `final_answer` / `fatal` / `done`。
其中 `phase` 和 `assistant_delta` 只走 UI 不入审计链；其余整段落审计。
同一步骤的 verification/confirm/execution 事件用 `step_id` 关联。

## 5. 关键设计决策与原因（改动前先理解）

- **不用 LangChain**：规避 LoongArch 依赖风险与"拼装货"质疑，核心自研千行级。
- **密码用 PBKDF2 而非 bcrypt**：bcrypt 是 C 扩展，LoongArch pip 源可能没轮子；
  PBKDF2 是 stdlib，零依赖。
- **规则引擎 argv 级匹配**：源自对 Codex CLI / Claude Code 权限模型的调研——
  字符串通配符匹配脆弱易绕过，argv 级 + 命令+参数级白名单更稳。
- **双 LLM 交叉校验**：审查员系统提示词独立、不看规划推理过程、不接收工具输出原文
  （Dual-LLM 红线，避免把注入面引入第二道闸）。
- **哈希链审计**：每条事件带前序哈希；**审计写入失败视为致命，立即中止任务**
  （不允许"干了但没记录"）。
- **正交分层**：门控决定"何时问人"（审批时机），执行器决定"技术上能做什么"
  （能力边界），互为纵深防御。
- **任何不确定收敛到"不执行"**：JSON 解析失败、LLM 失败、审查失败——全部 fail closed。

## 6. 里程碑进度

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| M1 | 骨架 + 五阶段流水线 + 3 插件 + run_command + 最小对话页，端到端打通 | ✅ 完成 |
| M2 | 6 插件 + 登录鉴权 + 审计回放 + 策略管理 + 仪表盘 + 多轮/流式对话 | ✅ 完成 |
| M3 | LoongArch 实机部署、install.sh + systemd、sudoers 白名单、性能实测 | ⬜ 未开始 |
| M4 | 安全对抗测试集(拦截率报告) + 5 份文档 + PPT + ≤7 分钟演示视频 | ⬜ 未开始 |

## 7. 未完成清单（下一步工作）

### M3（**阻塞于外部依赖：LoongArch 虚拟机镜像尚未批下来**，通过答疑 QQ 群申请）

- [ ] `install.sh`：依赖从 pip 源在线安装（赛题要求第三方依赖不打进安装包）。
- [ ] systemd 服务单元。
- [ ] 创建 `kylinguard-exec` 专用执行账户 + `/etc/sudoers.d/kylinguard` 精确白名单
      （精确到命令+参数，绝不 ALL）。
- [ ] **审计数据库与策略文件对执行账户设为只读**（防 Agent 篡改自己的审计链，
      调研结论，见 `executor.py` 文件头注释）。
- [ ] 在 LoongArch 麒麟 V11 实机验证依赖安装与端到端运行；性能实测
      （并发会话、快照耗时、端到端延迟）。

### M4

- [ ] 安全对抗测试集：约 30-50 条攻击用例（间接提示词注入、危险命令变体如
      base64/变量拼接绕过、越权尝试），产出**拦截率报告**（比赛亮点）。
- [ ] 5 份文档：需求分析、功能设计、产品说明书、功能测试报告、性能测试报告。
- [ ] PPT + ≤7 分钟演示视频（主线：对话运维走查 → 审计回放 → 提示词注入被拦截对抗演示）。

## 8. 已知技术债（不阻塞功能，但要知道）

- 前端刷新会丢失进行中任务的 SSE 流（无断线重连）。
- 多轮 conversation 无长度压缩，超长会话可能撑爆上下文窗口。
- 登录 token 存内存，服务重启需重新登录（单机演示够用）。
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
- 安全相关改动前先读第 5 节的设计原因，别破坏"fail closed"与"三道闸"不变量。
- 联调需要真实 systemctl/journalctl 时用 WSL 或 Linux，不要在 Windows 上判断
  Linux 命令行为。
