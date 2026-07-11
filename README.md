# 麒盾 KylinGuard

面向麒麟操作系统的安全智能运维 Agent —— 第十五届"中国软件杯"信创专项 A2 赛题参赛作品。

管理员用自然语言下达运维指令，Agent 经**感知 → 规划 → 校验 → 执行 → 溯源**
五阶段流水线完成任务。规则引擎保护 KylinGuard 控制面，独立 LLM Reviewer
提供风险判断，权限模式决定何时需要人确认。系统提供**只读 / 确认后执行 /
信任目录 / 完全访问**四种会话权限；完全访问具备完整 Agent 能力，高风险模式
切换由后端复验管理员密码，全程写入可检测篡改、可回放的哈希链审计日志。

## 快速开始（WSL 后端 + Windows 前端）

要求：WSL2 中安装 Python ≥ 3.10，Windows 中安装 Node ≥ 18，并可访问 OpenAI 兼容 LLM API（DeepSeek/Qwen）。
后端虚拟环境放在 WSL 的 ext4 主目录，避免在 `/mnt/*` 下安装大量 Python 小文件导致性能显著下降。

    # 1. 在 WSL 中进入仓库并配置项目
    cd /mnt/d/Documents/Study/3.3/cnsoft-projects
    cp .env.example .env
    # 必填：KG_LLM_API_KEY、KG_ADMIN_PASSWORD

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

浏览器打开前端地址 → 登录（用户名 `admin` + 你设置的密码）→ 顶栏切换五个视图：
**任务 / 审计 / 权限与安全 / 总览 / 告警**。模型连接、重试、权限请求、风险复核和工具执行状态会在任务中实时显示。

任务输入框旁可以随时查看或切换权限。新会话默认“确认后执行”；“信任目录”
仅让结构化 `files.*` 工具在选定目录内自动创建和修改文件，删除和终端命令
仍需确认。“完全访问”默认
可选，开启后 Agent 可使用完整 shell、文件、网络与进程能力，不再逐项询问。
它以界面显示的 OS 身份运行，不会自动获得 root；KylinGuard 的 LLM 密钥和
管理员口令不会下发到工具子进程。开启时必须复验管理员密码，并会在 TTL 到期
后自动收回；若使用后端当前身份，界面会明确提示它不具备 OS 账户级隔离。

新任务还可在输入框下方选择后端/WSL 可见的工作目录，例如
`/mnt/d/Documents/project`。该目录会持久化到任务，成为终端默认 `cwd` 并明确
进入模型上下文；任务创建后锁定，避免对话中途悄悄切换项目。它不是浏览器本地
文件夹选择器，也不是安全沙箱：完全访问仍可在 OS 身份允许时访问其他路径。

## Docker 启动（推荐快速体验）

要求：已安装 Docker 与 Docker Compose 插件（`docker compose`）。

最简单的方式：

    ./start.sh

`start.sh` 会自动检查：
- `docker` / `docker compose` 是否可用
- `.env` 是否存在
- `KG_LLM_API_KEY` 与 `KG_ADMIN_PASSWORD` 是否仍是模板占位值

若你想手动执行，也可以：

    # 1. 配置：复制模板并填写至少两个必填项
    cp .env.example .env
    #   必填：KG_LLM_API_KEY、KG_ADMIN_PASSWORD

    # 2. 构建并后台启动
    docker compose up -d --build

    # 3. 查看状态 / 停止
    docker compose ps
    docker compose logs -f
    docker compose down

浏览器访问 `http://127.0.0.1:8000`。

说明：
- Docker 方案会把 SQLite 数据库存到命名卷 `kylinguard-data`。
- 可写文件工作区挂载为命名卷 `kylinguard-workspace`，容器内路径 `/workspace`；
  容器根文件系统保持只读，进程使用非 root 账户且移除 Linux capabilities。
- 容器内默认把 `KG_DB_PATH` 设为 `/app/data/kylinguard.db`，避免相对路径歧义。
- 本项目的系统观测/服务管理能力面向 Linux 主机设计；放进普通容器后，`systemctl`、`journalctl` 等宿主机级能力会降级为容器内视角或采集失败，这属于预期。

> Linux 专属命令（systemctl、journalctl、free 等）在 Windows 上会显示"采集失败"降级，
> 属预期；在 WSL 或 Linux 下运行即真实工作。麒麟目标环境为 systemd 发行版。

## 目录结构

    backend/kylinguard/          Agent 核心（流水线、权限内核、三道闸、审计链、鉴权、会话）
    backend/kylinguard/plugins/  7 个领域 MCP 插件 + run_command（含结构化 files）
    backend/tests/               pytest 测试
    frontend/src/views/          五个视图页（任务/审计/策略/总览/告警）
    frontend/src/components/      可复用组件（步骤行/确认卡/markdown/侧栏/状态面板）
    frontend/src/composables/    前端状态层（useChat / usePermissions / useAuth）
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
  静态分类、与规划模型隔离的第二 LLM 风险审查、权限门控；完全访问是管理员
  复验后的明确信任边界，会跳过在线 Reviewer 和逐项确认，仅保留无效参数等
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

这不是开启完全访问的前置条件。每次开启仍必须重新验证当前管理员密码，并受
`KG_FULL_ACCESS_MAX_TTL` 限时约束；设置 `KG_ALLOW_FULL_ACCESS=false` 可由服务端
彻底关闭该模式。完整 shell 不继承任何 `KG_*` 控制面变量，结构化插件使用更小的
固定环境。完全访问不会跨后端重启继承，重启后必须重新复验。界面只陈述执行账户
是否与后端为不同 UID；真正的控制面隔离仍需部署方设置文件权限/ACL，不能仅凭
“不同 UID”推断。
