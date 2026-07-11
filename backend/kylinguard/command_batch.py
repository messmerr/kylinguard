"""把有限的复合命令语法转换为逐条 argv，不调用 shell。"""

from __future__ import annotations

import shlex
from dataclasses import dataclass


class CommandSyntaxError(ValueError):
    """命令文本无法安全转换为结构化批处理。"""


@dataclass(frozen=True)
class CommandBatch:
    commands: list[list[str]]
    operators: list[str]


def parse_simple_batch(text: str) -> CommandBatch:
    """解析简单命令及 ``;``/``&&``/``||`` 连接。

    引号中的符号按普通参数保留。管道、重定向、后台任务、命令替换、
    变量展开和换行控制结构均不在这个 argv 执行器的语义内，明确拒绝，
    避免把“不支持”误装成可以安全执行的 shell。
    """
    source = text.strip()
    if not source:
        raise CommandSyntaxError("命令为空")

    segments: list[str] = []
    operators: list[str] = []
    start = 0
    quote: str | None = None
    escaped = False
    index = 0

    while index < len(source):
        char = source[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            escaped = True
            index += 1
            continue
        if quote:
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char in {"`", "\n", "\r"} or source.startswith("$(", index):
            raise CommandSyntaxError("不支持命令替换、反引号或多行 shell 语法")
        if char in {"<", ">"}:
            raise CommandSyntaxError("不支持 shell 重定向；请使用结构化文件工具")
        if char == ";":
            operator = ";"
            width = 1
        elif source.startswith("&&", index):
            operator = "&&"
            width = 2
        elif source.startswith("||", index):
            operator = "||"
            width = 2
        elif char == "|":
            raise CommandSyntaxError("不支持 shell 管道；请拆成结构化步骤")
        elif char == "&":
            raise CommandSyntaxError("不支持后台 shell 任务")
        else:
            index += 1
            continue

        segment = source[start:index].strip()
        if not segment:
            raise CommandSyntaxError("连接符前后必须是完整命令")
        segments.append(segment)
        operators.append(operator)
        index += width
        start = index

    if quote or escaped:
        raise CommandSyntaxError("命令引号或转义未闭合")
    tail = source[start:].strip()
    if not tail:
        raise CommandSyntaxError("连接符后缺少命令")
    segments.append(tail)

    commands: list[list[str]] = []
    for segment in segments:
        try:
            argv = shlex.split(segment)
        except ValueError as exc:
            raise CommandSyntaxError(f"命令无法解析：{exc}") from exc
        if not argv:
            raise CommandSyntaxError("批处理中包含空命令")
        commands.append(argv)
    return CommandBatch(commands=commands, operators=operators)

