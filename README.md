# 麒盾 KylinGuard

面向麒麟操作系统的安全智能运维 Agent —— 第十五届"中国软件杯"信创专项 A2 赛题参赛作品。

管理员用自然语言下达运维指令，Agent 经**感知 → 规划 → 校验 → 受限执行 → 溯源**
五阶段安全流水线完成任务：每个步骤经三道闸（规则引擎、独立 LLM 审查员、
风险分级门控）校验，低危自动放行、中危一键确认、高危二次确认，
全程写入哈希链审计日志，防篡改、可回放。

## 快速开始（开发环境）

要求：Python ≥ 3.10、Node ≥ 18、可访问的 OpenAI 兼容 LLM API（DeepSeek/Qwen）。

    # 1. 配置密钥
    cp .env.example .env   # 填入 KG_LLM_API_KEY

    # 2. 后端
    cd backend
    pip install -e ".[dev]"
    pytest                  # 全量测试
    uvicorn --factory kylinguard.api:create_app --port 8000

    # 3. 前端（另开终端；或 npm run build 后直接访问后端 8000 端口）
    cd frontend
    npm install
    npm run dev             # http://127.0.0.1:5173

## 目录结构

    backend/kylinguard/          Agent 核心（五阶段流水线、三道闸、审计链）
    backend/kylinguard/plugins/  MCP 插件（stdio 服务器）
    backend/tests/               pytest 测试
    frontend/                    Vue3 + Element Plus 控制台

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

> 当前为 M1 里程碑（端到端打通）。登录鉴权、审计回放 UI、策略管理、
> 其余插件与 LoongArch 部署脚本在 M2/M3 落地。
