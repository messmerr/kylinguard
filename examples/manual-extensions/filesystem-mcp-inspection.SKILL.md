---
id: filesystem-mcp-inspection
name: Filesystem MCP 巡检
description: 当用户要验证第三方 Filesystem MCP、查看测试目录或读取测试文件时使用；先确认允许目录，再完成只读检查。
version: "1.0.0"
required_tools:
  - filesystem-demo.list_allowed_directories
  - filesystem-demo.list_directory
  - filesystem-demo.read_text_file
---
# 目标

验证第三方 Filesystem MCP 已正确连接，并用可复核的结果说明测试目录中有哪些文件。

# 工作流

1. 先调用 `filesystem-demo.list_allowed_directories`，确认服务只能访问配置的测试目录。
2. 调用 `filesystem-demo.list_directory` 查看测试目录内容。
3. 用户要求读取示例内容时，调用 `filesystem-demo.read_text_file` 读取 `hello.txt`。
4. 本次验证保持只读；除非用户明确要求，不创建、修改、移动或删除文件。

# 输出要求

简要列出允许目录、发现的文件和 `hello.txt` 内容，并说明是否成功调用了第三方 MCP。
