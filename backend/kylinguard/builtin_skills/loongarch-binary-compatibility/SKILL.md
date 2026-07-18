---
name: LoongArch 程序启动兼容性诊断
description: 面向银河麒麟 LoongArch64 上程序无法执行、Exec format error、解释器缺失、迁移后启动失败和架构疑似不匹配，安全读取 ELF 头、程序解释器和软件包归属形成分层结论；刻意不运行目标，也不使用可能执行不可信程序的 ldd。
version: "1.0.0"
required_tools:
  - kylin.system_identity
  - kylin.binary_compatibility
  - kylin.deployment_readiness
enabled: true
---
# 适用范围

用于已经有明确文件路径的程序、解释器或服务可执行文件。典型信号包括 `Exec format error`、`cannot execute binary file`、`wrong ELF class`、`No such file or directory` 但文件实际存在，以及从 x86_64/AArch64 迁移到 LoongArch64 后无法启动。

本 Skill 不负责反编译、运行样本、下载替代二进制、安装软件包或修改解释器链接。

# 强制原则

1. 必须取得精确绝对路径。只有程序名称、包名或服务名时，先请求路径或从可信的服务状态证据中取得；不得猜 `/usr/bin/<name>`。
2. `file` 描述、ELF Machine、主机架构、程序解释器和包归属是不同证据，必须分别呈现。
3. `architecture_match=true` 只是必要条件，不证明 ABI、glibc、共享库、内核接口、许可证或业务配置兼容。
4. “No such file or directory” 可能指 ELF 解释器不存在，不一定是目标文件不存在。只有工具明确取得 interpreter 并确认缺失时才能下此结论。
5. 不调用 `ldd`，不运行目标文件，不执行其 `--version`，不加载插件，不尝试修复。安全优先于补齐所有动态库结论。
6. 非 ELF 目标可能是脚本、文本、归档或损坏文件。此时停止 ELF 架构推断，不能把 `architecture=unknown` 写成“不兼容”。

# 工作流

## 1. 验证目标主机

调用 `kylin.system_identity`，记录：

- 是否为银河麒麟；
- 主机标准化架构，重点确认 `loongarch64`；
- 版本和 glibc 证据；
- 赛题目标匹配状态。

如果主机本身不是 LoongArch64，仍可完成文件检查，但标题应写“当前主机架构兼容检查”，不能声称是在目标平台验证。

当问题涉及项目自身构建依赖或 Node/Python 启动时，再调用 `kylin.deployment_readiness`，区分“系统二进制不兼容”和“项目构建工具缺失”。

## 2. 安全检查目标文件

调用 `kylin.binary_compatibility(path=<明确绝对路径>)`。读取：

- `file_description`；
- `elf.is_elf`、Class、Data、Machine、标准化 architecture；
- `host_architecture` 与 `architecture_match`；
- 请求的 interpreter 及其是否存在；
- rpm/dpkg 软件包归属或 unmanaged 状态；
- findings、evidence 和 limitations。

工具拒绝 `/proc`、`/sys`、`/dev`、`/run` 或控制字符路径时，不得改用通用 shell 绕过。

## 3. 按证据分类

### A. 明确架构不匹配

同时满足：目标为 ELF、Machine 可解析、主机架构可解析、`architecture_match=false`。可以确认“该 ELF 不能作为当前主机的本地架构程序直接执行”。报告目标架构与主机架构，不要仅写“版本不对”。

### B. ELF 解释器缺失

工具取得明确 interpreter 且 `interpreter_exists=false`。可以确认加载入口缺失，但不能立即断言应该创建软链接；解释器路径与 ABI 绑定，错误链接可能造成更隐蔽问题。

### C. 架构匹配但仍无法启动

如果 `architecture_match=true`：

- 明确排除“明显的 ELF 主机架构不匹配”；
- 不能声称共享库完整；
- 建议下一步检查包完整性、动态库解析、权限、挂载 `noexec`、服务运行用户和应用配置；
- 当前安全工具未使用 ldd，应把动态库状态标为未确认。

### D. 非 ELF

如果 `elf.is_elf=false`：

- file 描述为脚本时，问题可能在 shebang 解释器、换行符或执行权限；本轮没有脚本内容证据时只列为待验证；
- 描述为归档/数据时，说明它不是可直接执行的 ELF；
- file/readelf 输出冲突时保留冲突，不自行选择。

### E. 软件包归属

- 有 rpm/dpkg owner：说明目标由哪个已安装包提供，便于后续做包完整性核验；
- unmanaged：只能说明包管理器未确认归属，不能写“文件恶意”或“手工安装”；
- 不自动执行重装、降级或替换包。

## 4. 形成结论层级

- **已确认不兼容**：架构明确不匹配；
- **已确认加载入口缺失**：解释器路径明确且不存在；
- **排除明显架构问题**：架构匹配，但其他兼容层未验证；
- **目标不是 ELF**：转入脚本/文件类型诊断；
- **无法判断**：file/readelf 失败、Machine 未知或主机架构未知。

不得用“LoongArch64 包名”“文件位于 lib64”或文件名后缀代替 ELF Machine 证据。

# 停止条件

- 没有精确绝对路径：停止并请求路径；
- 目标不可读或不存在：报告错误，不搜索整个文件系统猜测副本；
- 目标位于受拒绝的伪文件系统/设备目录：停止，不绕过；
- 非 ELF：停止 ELF 结论，提出下一类只读验证建议；
- 需要运行目标、调用 ldd、下载文件或修改链接才能继续：停止并说明安全边界；
- 已确认架构不匹配：无需继续猜共享库问题，优先建议取得 LoongArch64 构建或从源码重建。

# 输出格式

1. **兼容性结论**：已确认不兼容、解释器缺失、排除明显架构问题、非 ELF 或无法判断；
2. **主机证据**：银河麒麟版本、主机架构、内核/glibc；
3. **目标证据**：file、ELF Class/Data/Machine、解释器；
4. **架构对比**：目标与主机逐项对照；
5. **软件包归属**：已确认包或未管理/未知；
6. **已排除与未确认**：尤其说明未使用 ldd、未运行目标；
7. **安全下一步**：重新获取 LoongArch64 构建、从源码构建或做独立包完整性验证。
