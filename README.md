# 麒盾 KylinGuard

面向麒麟操作系统的安全智能运维 Agent，第十五届“中国软件杯”信创专项 A2 赛题参赛作品。

管理员使用自然语言描述运维目标，KylinGuard 按照**感知 → 规划 → 校验 → 执行 → 溯源**五阶段流程完成任务。系统在保留完整运维能力的同时，通过静态规则、独立 LLM Reviewer、权限门控和哈希链审计约束大模型执行风险。

## 核心能力

- **系统感知**：采集主机、服务、日志、网络、磁盘和安全状态，为规划提供实时上下文。
- **安全规划**：使用 OpenAI 兼容模型生成结构化任务计划，支持流式展示执行过程。
- **三道安全闸**：静态风险分类、独立 Reviewer 复核、权限与动作授权共同决定允许、确认或拒绝。
- **四种审批模式**：只读、确认后执行、自动审核、完全访问，适配从巡检到复杂处置的不同场景。
- **可控执行**：通过 MCP 工具提供结构化运维能力，同时保留受权限体系约束的完整 Shell。
- **可信审计**：关键事件写入 SQLite WAL 数据库，并通过 SHA-256 哈希链支持完整性校验和任务回放。
- **可扩展能力**：支持在图形界面管理 OpenAI 兼容模型、自定义 stdio MCP 服务和声明式 Skills。

## 环境要求

目标环境为麒麟/Linux systemd 系统，建议配置：

- Python 3.10 或更高版本；
- Node.js 18 或更高版本及 npm；
- Bash；
- 可访问的 OpenAI、DeepSeek、DashScope 或其他 OpenAI 兼容模型服务。

`systemctl`、`journalctl`、`free` 等系统命令依赖 Linux 环境。请勿使用普通容器或 Windows 后端评估主机级运维能力。

## 安装与启动

以下命令均从仓库根目录执行。保持这个工作目录可以确保默认数据库写入 `data/`，并让后端正确发现构建后的前端。

```bash
# 1. 创建 Python 环境
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e "./backend"

# 2. 安装并构建前端
npm --prefix frontend ci
npm --prefix frontend run build

# 3. 启动服务
python -m uvicorn --factory kylinguard.api:create_app \
  --host 127.0.0.1 --port 8000
```

浏览器访问 `http://127.0.0.1:8000`。

首次使用时，在“模型服务”页面完成以下配置：

1. 添加模型提供商及 API Key；
2. 添加或发现可用模型；
3. 分别选择 Agent 默认模型和独立 Reviewer 模型；
4. 返回任务页面创建任务并选择工作目录。

模型 API Key 是只写字段，使用受限文件单独保存，不写入项目 `.env` 或审计数据库。

## 可选环境配置

应用不依赖 `.env` 才能启动。需要覆盖默认配置时，可复制模板后修改：

```bash
cp .env.example .env
```

常用配置：

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `KG_DB_PATH` | `data/kylinguard.db` | SQLite 数据库路径 |
| `KG_WORKSPACE_ROOT` | 仓库根目录 | Agent 默认工作目录 |
| `KG_COMMAND_SHELL` | `/bin/bash` | 完全终端使用的 Shell |
| `KG_COMMAND_TIMEOUT` | `30` | 普通命令超时秒数 |
| `KG_COMMAND_MAX_TIMEOUT` | `900` | Agent 可请求的最大命令超时 |
| `KG_LLM_TIMEOUT` | `60` | 单次模型请求超时秒数 |
| `KG_ALLOW_FULL_ACCESS` | `true` | 是否允许管理员揭示完全访问入口 |

模型提供商、API Key、可用模型和默认模型只通过“模型服务”页面管理。

## 权限与安全模型

```text
用户目标
  ↓
环境感知 → LLM 结构化规划
  ↓
静态规则 → 独立 Reviewer → 权限门控
  ↓
MCP / Shell 执行
  ↓
哈希链审计与任务回放
```

四种全局审批模式：

- **只读**：只允许读取和诊断，不执行修改操作。
- **确认后执行**：修改操作在管理员确认后执行，默认使用此模式。
- **自动审核**：静态规则与 Reviewer 通过后，自动执行授权范围内的可逆操作。
- **完全访问**：在当前 OS 身份权限范围内开放完整 Shell、文件、网络和进程能力。

完全访问入口默认隐藏，揭示和启用分别需要醒目警告及二次输入确认。高风险动作授权绑定任务、权限版本、资源和动作指纹；审计写入失败会中止对应操作。

## MCP 与 Skills

内置 MCP 工具覆盖：

- 主机与资源信息；
- systemd 服务管理；
- journal 日志查询；
- 网络连接与端口检查；
- 磁盘分析与受限清理；
- 安全基线检查；
- 结构化文件操作；
- 受权限门控的通用命令执行。

内置 Skills 位于 `backend/kylinguard/builtin_skills/`，随 Python 包分发。自定义 stdio MCP 和 Skills 可在“扩展”页面添加、测试、启停；第三方工具仍须经过现有风险、权限和审计边界。

## 开发与验证

安装测试依赖：

```bash
source .venv/bin/activate
python -m pip install -e "./backend[dev]"
```

运行完整验证：

```bash
# 后端测试
python -m pytest backend/tests

# 前端测试与生产构建
npm --prefix frontend test
npm --prefix frontend run build

# 60 例 OS Agent 安全基准
python tools/run_security_benchmark.py
```

前端开发模式：

```bash
# 终端一：后端热更新
python -m uvicorn --factory kylinguard.api:create_app \
  --host 127.0.0.1 --port 8000 --reload

# 终端二：Vite 开发服务器
npm --prefix frontend run dev
```

Vite 默认把 `/api` 代理到 `http://127.0.0.1:8000`。

## 项目结构

```text
backend/
  kylinguard/                 FastAPI、Agent 流水线、安全门控、审计与 MCP
  tests/                      后端测试
benchmarks/                   OS Agent 安全基准数据集
frontend/
  src/                        Vue 3 页面、组件与状态层
  tests/                      前端逻辑测试
tools/                        安全基准命令行工具
```

## 使用边界

- 当前控制台面向单机管理员，不提供远程登录认证；默认仅监听 `127.0.0.1`。
- 完全访问不会自动获得 root，实际能力由启动后端的 OS 用户权限决定。
- 工作目录是任务上下文和默认执行目录，不是独立安全沙箱。
- 命令执行为非交互模式，不提供 PTY 或持续标准输入。
