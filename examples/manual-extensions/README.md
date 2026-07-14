# MCP 与 Skill 手工测试

本目录提供两个官方 MCP 配置和两个符合 Agent Skills 格式的单文件 Skill。示例不会自动安装、保存或启用。

## 1. 添加 MCP

1. 打开“扩展”页面，点击“添加 MCP”。
2. 复制 `mcp-official-examples.json` 的完整内容并粘贴。
3. 先选择 `filesystem-demo`，继续并保存；回到列表后点击“测试”，成功后再启用。
4. 重复添加，选择 `sequential-thinking-demo`。

首次测试时 `npx` 会下载指定版本的官方 npm 包，可能需要等待数秒。配置中的 `npx` 是这台 Mac 当前的绝对路径；Node 版本变化后可用 `command -v npx` 获取新路径。

## 2. 添加 Skill

分别点击“添加 Skill”并选择：

- `filesystem-mcp-inspection.SKILL.md`
- `change-plan-review.SKILL.md`

保存后在列表中启用。工具依赖应在对应 MCP 启用后显示为可用。

## 3. 建议测试任务

### 自动匹配 Filesystem Skill

> 检查 MCP 测试目录里有什么，并告诉我 hello.txt 的内容。

预期：模型自动选择“Filesystem MCP 巡检”，请求调用 `filesystem-demo` 工具，并在权限确认后读出测试文字。

### 使用 @ 明确指定 Skill

在输入框中输入 `@`，选择“Filesystem MCP 巡检”，然后补充：

> 只读检查测试目录，不要创建或修改文件。

预期：正文中保留 Skill 标签，本轮按指定 Skill 执行。

### 自动匹配复杂方案评审

> 规划 PostgreSQL 14 升级到 16，停机时间不能超过 5 分钟；如果做不到，请给出备选路线和回滚条件。

预期：模型选择“复杂变更方案评审”，调用 `sequential-thinking-demo.sequential_thinking`，最终给出结构化方案，不执行任何系统修改。

## 来源

- Filesystem MCP：https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem
- Sequential Thinking MCP：https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking
- Agent Skills 规范：https://github.com/agentskills/agentskills
- Anthropic Skill 示例库：https://github.com/anthropics/skills
