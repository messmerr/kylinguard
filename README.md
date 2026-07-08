# 麒盾 KylinGuard

面向麒麟操作系统的安全智能运维 Agent —— 第十五届"中国软件杯"信创专项 A2 赛题参赛作品。

管理员用自然语言下达运维指令，Agent 经**感知 → 规划 → 校验 → 受限执行 → 溯源**
五阶段安全流水线完成任务：每个步骤经三道闸（规则引擎、独立 LLM 审查员、
风险分级门控）校验，低危自动放行、中危一键确认、高危二次确认，
全程写入哈希链审计日志，防篡改、可回放。

## 快速开始（开发环境）

要求：Python ≥ 3.10、Node ≥ 18、可访问的 OpenAI 兼容 LLM API（DeepSeek/Qwen）。

    # 1. 配置：复制模板并填入 LLM 密钥与管理员密码
    cp .env.example .env
    #   必填：KG_LLM_API_KEY（LLM 密钥）、KG_ADMIN_PASSWORD（登录密码，留空则无法登录）

    # 2. 后端
    cd backend
    pip install -e ".[dev]"
    pytest                  # 全量测试（约 180 个用例）
    uvicorn --factory kylinguard.api:create_app --port 8000

    # 3. 前端（先构建，之后由后端直接托管在 8000 端口）
    cd frontend
    npm install
    npm run build           # 产物 dist/ 由后端静态托管
    #   开发热更新可改用 npm run dev（http://127.0.0.1:5173，自动代理 /api 到 8000）

浏览器打开后端端口 → 登录（用户名 `admin` + 你设置的密码）→ 顶栏切换四个视图：
**对话运维 / 审计回放 / 策略管理 / 仪表盘**。

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
- 容器内默认把 `KG_DB_PATH` 设为 `/app/data/kylinguard.db`，避免相对路径歧义。
- 本项目的系统观测/服务管理能力面向 Linux 主机设计；放进普通容器后，`systemctl`、`journalctl` 等宿主机级能力会降级为容器内视角或采集失败，这属于预期。

> Linux 专属命令（systemctl、journalctl、free 等）在 Windows 上会显示"采集失败"降级，
> 属预期；在 WSL 或 Linux 下运行即真实工作。麒麟目标环境为 systemd 发行版。

## 目录结构

    backend/kylinguard/          Agent 核心（五阶段流水线、三道闸、审计链、鉴权、会话、策略）
    backend/kylinguard/plugins/  6 个 MCP 插件 + run_command（stdio 服务器）
    backend/tests/               pytest 测试
    frontend/src/views/          四个视图页（对话/审计/策略/仪表盘）
    frontend/src/components/      可复用组件（步骤行/确认卡/markdown/侧栏/状态面板）
    frontend/src/composables/    前端状态层（useChat 事件聚合 / useAuth 鉴权）
    HANDOFF.md                   项目交接文档（当前状态、进度、未完成清单）

## 架构与安全设计

详见设计文档（比赛交付材料）。要点：

- **不可控性驯服**：模型可自由拟定命令（`run_command`），但每条命令过
  三道闸——argv 级静态规则引擎（黑名单/保护路径/元字符/命令+参数级
  只读白名单/载荷执行器防御，解析失败一律 fail closed）+ 与规划模型
  完全隔离的第二 LLM 审查 + 风险分级门控；任何不确定收敛到"不执行"。
- **最小权限**：命令不经 shell 执行（元字符无从生效）；生产环境经
  `sudo -n -u kylinguard-exec` 降权 + sudoers 精确白名单；审计库与
  策略文件对执行账户只读（Agent 不能篡改自己的审计链）。
- **防篡改审计**：每条事件携带前序事件哈希；审计写入失败立即中止任务。
- **正交分层**（参照 Codex CLI 安全模型）：门控决定"何时问人"（审批
  时机），受限执行器决定"技术上能做什么"（能力边界），互为纵深防御。

> 当前进度：**M1 + M2 完成**（功能层面全部就绪）。剩余 M3（LoongArch 实机部署、
> install.sh/systemd、性能实测）与 M4（安全对抗测试集、5 份文档、PPT、演示视频）。
> 详细状态、架构地图与未完成清单见 [HANDOFF.md](HANDOFF.md)。
