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

### 扩展：MCP 服务与 Skills

“扩展”页面集中管理可由 Agent 使用的自定义 MCP 工具服务和 Skill 工作流。
二者是不同层次的能力：MCP 服务提供实际工具，Skill 为一类任务提供可复用的
步骤、证据要求和输出规范。内置工具与内置 Skill 不需要额外配置；自定义项的
创建、修改、启停、测试和删除都会写入扩展审计记录。

#### 自定义 stdio MCP

在“扩展 → MCP 服务”中填写服务 ID、名称、**绝对路径**启动命令、可选工作目录、
逐行参数和环境变量。工作目录也必须是后端可见的规范化绝对路径，且不能经过
符号链接；留空时使用启动命令所在目录。当前版本只支持本机 `stdio` MCP，不支持 HTTP/SSE 传输。命令直接以
argv 启动，不经过 shell；保存只写入配置，新服务默认停用。点击“测试”或启用
时才会在后端主机启动程序、完成 MCP 握手并读取工具清单，启用成功后无需重启
后端即可供后续任务使用。

“导入配置”只把常见的 `mcpServers` 包装 JSON、单个服务 JSON，或一条完整的
stdio 启动命令解析到编辑草稿中；它不会下载依赖，也不会自动保存、测试或启用
服务。配置中有多个服务时需先选择其中一个。远程 URL MCP 当前不受支持；导入
得到的 `npx`、`uvx` 等相对命令必须在保存前改成后端主机可见的绝对路径。
疑似 Token、API Key 或密码的环境变量会进入“敏感环境变量”只写字段。

普通环境变量会在设置页回显；Token、API Key 和密码必须填入“敏感环境变量”
只写字段。SQLite 只保存变量名和随机文件引用，值存放在
`KG_MCP_SECRETS_DIR` 下的 `0600` 文件中，API、审计和错误信息都不会回显。
未显式配置时，该目录位于
`~/.local/state/kylinguard/mcp-secrets/<数据库标识>`，而不跟随数据库进入
WSL 的 `/mnt/*` 等无法可靠表达 `0700/0600` 权限的挂载盘；Docker 部署仍使用
Compose 显式配置的持久卷路径。
自定义进程只获得固定最小环境以及管理员为该服务显式配置的变量，不继承
`KG_*` 控制面配置、模型密钥、动态加载器变量或后端工作目录。

自定义 MCP Server 本身是本机代码执行边界，只应启用来源可信、经过审查的
程序。“测试”同样会启动程序，不是静态校验。系统安装配置
`KG_EXEC_USER` 后，自定义 MCP 会降权到该独立账户；未配置时（包括默认
Docker/开发模式）它与后端共用 OS 身份，可读取该身份能访问的状态和秘密文件，
因此此时必须将 MCP 视为完全可信代码。服务声明的工具说明、annotations 和
返回内容都按不可信数据处理；新发现的自定义工具默认按高风险进入现有规则、
Reviewer、权限门控和哈希链审计。管理员可在服务停用时按工具显式设置低/中/
高风险；该设置绑定名称、说明、输入 Schema 与 annotations 的定义摘要，并在
每次实际启动、重新列举工具后复核。定义变化、策略损坏或摘要不匹配都会自动
回退为平台默认高风险；运行中的服务若通知工具目录已经变化，旧路由和授权身份
会立即失效，须重新测试和审核。Skill、MCP 自述或工具结果本身不能授予权限；
但管理员显式开启“完全访问”后，调用会遵循该模式原有的不逐项确认语义。

##### Docker 中的自定义 MCP

扩展页填写的绝对路径始终是**后端运行环境内**的路径。使用 Docker 时，宿主机的
`/usr/bin/npx`、Python 虚拟环境或项目路径不会自动出现在容器内；例如界面中的
命令应是容器可见的 `/opt/mcp/bin/server`，而不是宿主机路径。自定义 MCP 的
`cwd` 默认是该可执行文件的父目录，也可在扩展页明确设置为另一个容器内绝对
目录；它不会继承后端 `/app/backend` 工作目录。依赖相对文件的服务应把资源
放入镜像或只读挂载目录，再显式选择该目录。

官方基础镜像有意不预装 Node/npm，也不会在容器启动时在线安装 MCP 包：这既
避免为不使用 Node 的部署增加镜像体积，也避免 `npx` 在“测试/启用”阶段隐式
下载并执行未锁定的供应链代码。推荐在构建派生镜像时安装**固定版本**的 Node
MCP，并在扩展页直接填写安装后的入口，而不是运行时下载：

    # 先执行：docker build -t kylinguard:base .
    FROM kylinguard:base
    USER root
    RUN apt-get update \
        && apt-get install -y --no-install-recommends nodejs npm \
        && npm install --global --prefix /opt/mcp --omit=dev your-mcp-package@1.2.3 \
        && npm cache clean --force \
        && rm -rf /var/lib/apt/lists/*
    USER kylinguard

派生镜像中应在扩展页使用 `/opt/mcp/bin/<实际入口名>`。如果确实要使用
`/usr/bin/npx`，包名必须固定精确版本，并在该 MCP 的“普通环境变量”中显式配置
`NPM_CONFIG_CACHE=/tmp/npm-cache`、`NPM_CONFIG_UPDATE_NOTIFIER=false`；不得依赖
宿主机的 HOME、npmrc、代理或认证环境。Compose 的根文件系统为只读，只有
`/app/data`、`/workspace` 和 `/tmp` 可写，其中 `/tmp` 是 64 MiB 临时 tmpfs；
缓存超过上限会失败且容器重启后清空，不应把它作为包的持久安装位置。

另一种方式是在 Compose 覆盖文件中把已经安装好的、与容器架构和 libc 兼容的
服务包只读挂载进去：

    services:
      kylinguard:
        volumes:
          - ./mcp-runtime:/opt/mcp:ro

挂载包必须自带容器内可用的解释器/可执行文件及依赖；需要持久写入的数据应另行
挂载到专用目录，不要尝试写只读的入口目录。自定义 MCP 仍只获得最小环境和为
该服务显式填写的变量，不会自动继承宿主机环境。

#### 自定义 Skill

在“扩展 → Skills”中创建并启用 Skill。默认情况下，模型先看到已启用 Skill 的
名称和说明，并可根据管理员本轮任务自动匹配；只有选中后才加载完整正文。用户也
可以在任务正文的任意位置输入 `@`，把一个或多个 Skill 作为行内标签明确加入
本轮任务；未插入 Skill 标签时自然回到自动匹配。系统随包提供
“磁盘空间诊断”“systemd 服务故障排查”和“安全基线巡检”三个只读优先的示例。
每轮开始时会冻结最终所选 Skill 的名称、版本、内容哈希和可选依赖并写入审计；
运行中修改只影响下一轮。

- `required_tools` 是可选的 KylinGuard 依赖声明，只检查对应 MCP 工具是否已经安装
  并启用；缺失时不会启动该 Skill。它不授权工具，也不会限制本轮其他工具。
- Skill 是低于系统安全策略和管理员原始指令的工作流指导，不是新的执行通道。
  正文中出现的脚本、命令或外部内容不会被直接执行，仍必须规划成可用 MCP
  工具调用并经过原有校验、确认和审计。
- 同轮显式组合多个 Skill 时，系统按标签顺序组合工作流并合并检查依赖。Skill
  不改变工具目录；真正能否执行始终由 MCP 是否启用、风险规则、Reviewer、会话
  权限与人工确认共同决定。

从 `SKILL.md` 添加时只做单文件规范化转换，只采用可识别的元数据和 Markdown
指令，不会安装同目录的 `scripts/`、`references/`、`assets/` 或其他资源。新 Skill
默认停用，并需由管理员复核工作流正文后再启用。普通 Agent Skills/Codex 文件只需
`name`、`description` 和正文即可使用；旧文件中的 `manual_only` 仅作为兼容字段读取，
不再阻止自动匹配。Claude 的 `allowed-tools` 以及旧版麒盾的 `allowed_tools`、
`allow_all_tools` 会被作为来源专用运行配置忽略，绝不会转换成本地授权或工具限制。

同一个 `@` 菜单还可以引用当前服务器工作目录中的文件。Skill 与文件标签都直接
留在正文原位置，可在一轮中重复插入或单独删除；标签删除后对应的结构化引用也会
一起消失。前端只发送经过后端校验的相对路径，不会自动读取或上传正文；模型确有
需要时仍须通过可审计的 `files.*` 工具读取，并继续遵守会话权限和风险门控。

内置 Skill 位于只读的 `backend/kylinguard/builtin_skills/`。自定义 Skill 按
“一个一级目录一个 `SKILL.md`”保存，启停状态单独持久化：

    ${KG_SKILLS_DIR}/
      my-skill/
        SKILL.md
        references/             # 可选；当前不会自动注入提示词
        scripts/                # 可选；当前不会自动执行
    ${KG_SKILLS_STATE_PATH}     # Skill 启停状态 JSON
    ${KG_MCP_SECRETS_DIR}/      # 自定义 MCP 的敏感环境变量文件

`SKILL.md` 使用受限 YAML frontmatter 和 Markdown 正文；也可以直接通过图形界面
生成。最小示例：

    ---
    name: 日志故障排查
    description: 先读取服务状态和日志，再给出处置建议。
    version: "1.0.0"
    required_tools:
      - services.service_status
      - logs.journal_search
    enabled: false
    ---
    先收集同一时间窗口内的状态和日志证据。未经管理员明确要求，不执行修改。

未显式配置目录时，`skills/` 和 `skills-state.json` 跟随数据库目录；MCP 凭据
使用前述用户状态目录并按数据库隔离。生产部署应将三者放在只允许 KylinGuard
控制面账户访问的持久目录；Docker 和 `install.sh` 已提供对应配置。

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
- 宿主机端口只绑定 `127.0.0.1`。当前控制面没有远程身份认证，远程使用请优先
  `ssh -L 8000:127.0.0.1:8000 user@server`；不得直接暴露端口，也不得仅用无认证的
  HTTPS 反向代理对外开放。
- GUI 保存的模型 API Key 也位于该数据卷的受限独立目录；接口和数据库不回显 Key。
- 自定义 MCP 的只写秘密环境变量、自定义 Skill 和 Skill 启停状态同样位于
  `kylinguard-data` 数据卷；删除容器不会丢失，删除该命名卷会一并清除。
- 可写文件工作区挂载为命名卷 `kylinguard-workspace`，容器内路径 `/workspace`；
  容器根文件系统保持只读，进程使用非 root 账户且移除 Linux capabilities。
- 容器内默认把 `KG_DB_PATH` 设为 `/app/data/kylinguard.db`，避免相对路径歧义。
- 本项目的系统观测/服务管理能力面向 Linux 主机设计；放进普通容器后，`systemctl`、`journalctl` 等宿主机级能力会降级为容器内视角或采集失败，这属于预期。

> Linux 专属命令（systemctl、journalctl、free 等）在 Windows 上会显示"采集失败"降级，
> 属预期；在 WSL 或 Linux 下运行即真实工作。麒麟目标环境为 systemd 发行版。

## 目录结构

    backend/kylinguard/          Agent 核心（流水线、权限内核、三道闸、审计链、会话）
    backend/kylinguard/plugins/  7 个领域 MCP 插件 + run_command（含结构化 files）
    backend/kylinguard/builtin_skills/  随包分发的只读 Skill 工作流
    backend/tests/               pytest 测试
    frontend/src/views/          视图页（任务/模型服务/扩展/审计/策略/总览/告警）
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
  自动执行。引号或转义内的 `&` 只作为字面参数；每一段都能证明只读的
  `;`/`&&`/`||` 命令链会改写到无 shell 的 `run_batch`，真正的后台 `&`、
  管道、重定向和展开仍进入显式权限复核。默认工作目录是项目根。
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
