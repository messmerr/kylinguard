# 麒盾 KylinGuard

面向麒麟操作系统的安全智能运维 Agent —— 第十五届"中国软件杯"信创专项 A2 赛题参赛作品。

管理员用自然语言下达运维指令，Agent 经**感知 → 规划 → 校验 → 执行 → 溯源**
五阶段流水线完成任务。规则引擎保护 KylinGuard 控制面，独立 LLM Reviewer
提供风险判断，权限模式决定何时需要人确认。系统提供**只读 / 确认后执行 /
信任目录 / 完全访问**四种会话权限；完全访问具备完整 Agent 能力，高风险模式
切换与高风险操作均要求显式确认，全程写入可检测篡改、可回放的哈希链审计日志。

## 快速开始（WSL 后端 + Windows 前端）

要求：WSL2 中安装 Python ≥ 3.10，Windows 中安装 Node ≥ 18，并可访问 OpenAI 兼容 LLM API（DeepSeek/Qwen）。
后端虚拟环境放在 WSL 的 ext4 主目录，避免在 `/mnt/*` 下安装大量 Python 小文件导致性能显著下降。

    # 1. 在 WSL 中进入仓库并配置项目
    cd /mnt/d/Documents/Study/3.3/cnsoft-projects
    cp .env.example .env
    # 模型提供商和 API Key 可在界面配置

    # 2. 首次创建后端环境（只需执行一次）
    mkdir -p ~/.venvs
    python3 -m venv ~/.venvs/kylinguard
    source ~/.venvs/kylinguard/bin/activate
    python -m pip install -e "./backend[dev]"

    # 3. 运行后端
    cd backend
    python -m pytest        # 全量测试
    python -m uvicorn --factory kylinguard.api:create_app --host 127.0.0.1 --port 8000 --reload

    # 4. 另开一个 Windows PowerShell 运行前端
    cd D:\Documents\Study\3.3\cnsoft-projects\frontend
    npm ci                  # 首次运行或 package-lock.json 变化后执行
    npm run dev
    # 浏览器访问 http://127.0.0.1:5173，/api 自动代理到 8000

若不需要热更新，也可以在 Windows 的 `frontend` 目录执行 `npm run build`，再只启动 WSL 后端；构建产物会由 8000 端口直接托管。

浏览器打开前端地址 → 在“模型服务”添加 API 提供商、Key 和可用模型 →
回到任务页开始使用。主模型和推理强度可按
会话切换；安全 Reviewer 保持独立的全局配置。模型连接、重试、权限请求、风险复核
和工具执行状态会在任务中实时显示。

任务输入框旁可以随时查看或切换权限。新会话默认“确认后执行”；“信任目录”
仅让结构化 `files.*` 工具在选定目录内自动创建和修改文件，删除和终端命令
仍需确认。“完全访问”默认
可选，开启后 Agent 可使用完整 shell、文件、网络与进程能力，不再逐项询问。
它以界面显示的 OS 身份运行，不会自动获得 root；KylinGuard 的 LLM 密钥和
控制面配置不会下发到工具子进程。开启后会在 TTL 到期时自动收回；若使用后端
当前身份，界面会明确提示它不具备 OS 账户级隔离。

新任务还可在输入框下方选择后端/WSL 可见的工作目录，例如
`/mnt/d/Documents/project`。该目录会持久化到任务，成为终端默认 `cwd` 并明确
进入模型上下文；任务创建后锁定，避免对话中途悄悄切换项目。它不是浏览器本地
文件夹选择器，也不是安全沙箱：完全访问仍可在 OS 身份允许时访问其他路径。

### 模型服务配置

在“模型服务”中添加 OpenAI、DeepSeek、DashScope 或其他 OpenAI 兼容
提供商。API Key 是只写字段；模型可以手工维护，也可通过提供商的 `/models`
接口读取。由于该接口没有标准推理能力字段，OpenAI 与 OpenAI Compatible 默认
开放兼容性最广的 `low` / `medium` / `high` 三档，DeepSeek 使用官方的
`none` / `high` / `max` 语义；其他扩展
档位可在编辑提供商时批量启用并逐模型覆盖。`temperature` 能力仍需按模型说明
确认。新任务使用全局 Agent 默认值，安全 Reviewer 单独配置；任务建立后保存
自己的主模型，可在输入框旁切换，下一轮生效。
模型服务无需登录；提供商变更仍执行版本校验并进入审计日志。完全访问与高风险
操作仍保留明确确认、TTL、单次授权和审计，因为它们会扩大 Agent 的系统执行权限。

DeepSeek 的 `thinking/reasoning_effort`、DashScope 的
`enable_thinking/thinking_budget` 与 OpenAI 的 `reasoning_effort` 会由后端适配，
参考 [DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)、
[阿里云深度思考](https://help.aliyun.com/en/model-studio/deep-thinking) 和
[OpenAI 模型文档](https://developers.openai.com/api/docs/models)。会话固定模型和
能力随模型变化的交互也参考了
[Claude Code 模型配置](https://code.claude.com/docs/en/model-config)。

GUI Key 不进入 SQLite、审计、SSE 或工具子进程，而是保存到工作区外的受限独立
文件；数据库只保存随机引用。此处的文件权限不能替代账户隔离：完全访问与后端
共用同一 UID 时仍可能读取同身份文件，生产环境应配置独立 `KG_EXEC_USER`。
设置页会把这种情况标成“开发环境说明”；普通任务并未因此失效，它描述的是开启
完全访问后的隔离上限，而不是模型连接故障。
模型提供商、API Key、可用模型与 Agent/Reviewer 默认值只从图形界面的持久化
配置读取；`.env` 不再提供模型配置或回退路径。

## Docker 启动（推荐快速体验）

要求：已安装 Docker 与 Docker Compose 插件（`docker compose`）。

最简单的方式：

    ./start.sh

`start.sh` 会自动检查：
- `docker` / `docker compose` 是否可用
- `.env` 是否存在

若你想手动执行，也可以：

    # 1. 配置：复制模板
    cp .env.example .env

    # 2. 构建并后台启动
    docker compose up -d --build

    # 3. 查看状态 / 停止
    docker compose ps
    docker compose logs -f
    docker compose down

浏览器访问 `http://127.0.0.1:8000`。

说明：
- Docker 方案会把 SQLite 数据库存到命名卷 `kylinguard-data`。
- 宿主机端口只绑定 `127.0.0.1`。远程访问必须使用 HTTPS 反向代理或
  `ssh -L 8000:127.0.0.1:8000 user@server`，不要在局域网以 HTTP 明文提交
  模型 API Key。
- GUI 保存的模型 API Key 也位于该数据卷的受限独立目录；接口和数据库不回显 Key。
- 可写文件工作区挂载为命名卷 `kylinguard-workspace`，容器内路径 `/workspace`；
  容器根文件系统保持只读，进程使用非 root 账户且移除 Linux capabilities。
- 容器内默认把 `KG_DB_PATH` 设为 `/app/data/kylinguard.db`，避免相对路径歧义。
- 本项目的系统观测/服务管理能力面向 Linux 主机设计；放进普通容器后，`systemctl`、`journalctl` 等宿主机级能力会降级为容器内视角或采集失败，这属于预期。

> Linux 专属命令（systemctl、journalctl、free 等）在 Windows 上会显示"采集失败"降级，
> 属预期；在 WSL 或 Linux 下运行即真实工作。麒麟目标环境为 systemd 发行版。

## 目录结构

    backend/kylinguard/          Agent 核心（流水线、权限内核、三道闸、审计链、会话）
    backend/kylinguard/plugins/  7 个领域 MCP 插件 + run_command（含结构化 files）
    backend/tests/               pytest 测试
    frontend/src/views/          视图页（任务/模型服务/审计/策略/总览/告警）
    frontend/src/components/      可复用组件（步骤行/确认卡/markdown/侧栏/状态面板）
    frontend/src/composables/    前端状态层（useChat / useModels / usePermissions / useApi）
    HANDOFF.md                   项目交接文档（当前状态、进度、未完成清单）

## 架构与安全设计

详见设计文档（比赛交付材料）。要点：

- **结构化工具与完整 Shell 并存**：普通文件读写优先走 `files.*` 结构化工具，支持原子
  写入、哈希前置条件、大小限制，并用 `openat + O_NOFOLLOW` 封闭符号链接
  竞态；多条命令走逐 argv 的
  `run_batch`。通用终端使用 `KG_COMMAND_SHELL` 指定的完整 shell，支持脚本、
  管道、重定向、工作目录、网络工具及进程控制；普通模式按需询问，完全访问
  自动执行。默认工作目录是项目根。
- **不可控性驯服**：模型可自由拟定命令（`run_command`）。普通模式依次经过
  静态分类、与规划模型隔离的第二 LLM 风险审查、权限门控；完全访问是用户
  显式开启的信任边界，会跳过在线 Reviewer 和逐项确认，仅保留无效参数等
  执行协议边界，并完整记录审计。
- **权限与能力分离**：默认模式强调逐项授权，完全访问强调完整执行能力。
  两者都以 `KG_EXEC_USER`（若配置）或后端当前 OS 身份运行；完整 shell 保留
  当前身份的 Git/SSH/代理/虚拟环境等用户工具链环境，但剥离全部 `KG_*`
  控制面配置。结构化插件与只读 argv 通道使用固定系统 PATH 和最小环境，避免
  PATH/动态加载器劫持。执行器按进程组清理超时/取消任务，并对双流输出做
  固定内存预算。
- **可验证审计**：每条事件携带前序事件哈希；权限变更、授权消费与对应安全
  事件同 SQLite 事务提交，审计写入失败立即回滚并中止任务。
- **过程可见**：模型认证、限流重试、超时、风险复核和工具执行均以结构化
  事件实时反馈；错误只暴露清洗后的状态码与诊断编号，不传输密钥或原始请求。
- **模型运行快照**：提供商、可用模型和默认 Agent/Reviewer 可在界面管理；每轮
  在会话锁内冻结模型与推理强度，运行中修改只影响下一轮，并把不含凭据的模型
  上下文写入审计链。API Key 为只写字段，单独存放在工作区外的 `0600` 文件。
- **授权可撤销**：批准绑定会话、权限版本、能力、资源与动作指纹；支持仅一次、
  本会话和可信目录授权；一次授权只有收到其 grant id 的原等待步骤可消费，
  高风险永远只能单次授权。TTL 到期不可因系统时钟回拨复活。
- **正交分层**（参照通用编码 Agent 权限模型）：执行器提供完整终端能力，
  门控只决定"何时问人"；最终可做什么由明确授权与实际 OS 身份共同决定。

当前终端为非交互执行，不提供 PTY/持续 stdin；单次命令、输出大小和规划轮数仍有
明确上限。它面向可审计的服务器任务，不宣称与桌面编码 Agent 的所有交互能力完全等价。

> 当前进度：**M1 + M2 完成**（功能层面全部就绪）。剩余 M3（LoongArch 实机部署、
> install.sh/systemd、性能实测）与 M4（安全对抗测试集、5 份文档、PPT、演示视频）。
> 详细状态、架构地图与未完成清单见 [HANDOFF.md](HANDOFF.md)。

### 完全访问（高风险，默认可用）

完全访问不要求额外创建执行账户。开启后，Agent 在 `KG_WORKSPACE_ROOT`（默认项目根）
中拥有完整 shell、文件、网络和进程能力，不再对每个动作逐项确认。界面会明确显示
实际 OS 执行身份；若 `KG_EXEC_USER` 留空，就是后端当前身份。

    # 以下均为默认值
    KG_ALLOW_FULL_ACCESS=true
    KG_COMMAND_SHELL=/bin/bash
    KG_COMMAND_MAX_TIMEOUT=900
    KG_FULL_ACCESS_MAX_TTL=1800

如果部署方希望进一步做 OS 级隔离，仍可选择配置专用账户：

    KG_EXEC_USER=kylinguard-exec

这不是开启完全访问的前置条件。每次开启均受 `KG_FULL_ACCESS_MAX_TTL` 限时约束；
设置 `KG_ALLOW_FULL_ACCESS=false` 可由服务端
彻底关闭该模式。完整 shell 不继承任何 `KG_*` 控制面变量，结构化插件使用更小的
固定环境。完全访问不会跨后端重启继承，重启后需要重新开启。界面只陈述执行账户
是否与后端为不同 UID；真正的控制面隔离仍需部署方设置文件权限/ACL，不能仅凭
“不同 UID”推断。
