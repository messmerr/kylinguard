from types import SimpleNamespace

import pytest

from kylinguard.mcp_client import ToolCallError, ToolManager, split_qualified


def test_限定名拆分():
    assert split_qualified("sysinfo.top_processes") == ("sysinfo", "top_processes")
    with pytest.raises(ValueError):
        split_qualified("裸名无点号")


async def test_未知服务器与MCP错误结果显式失败():
    mgr = ToolManager()
    with pytest.raises(ToolCallError, match="未知工具服务器"):
        await mgr.call("missing", "tool", {})

    class FakeSession:
        async def call_tool(self, _tool, _arguments):
            return SimpleNamespace(
                isError=True,
                content=[SimpleNamespace(text="底层工具执行失败")],
            )

    mgr._sessions["fake"] = FakeSession()
    with pytest.raises(ToolCallError, match="底层工具执行失败"):
        await mgr.call("fake", "tool", {})


@pytest.mark.parametrize("text", [
    "[执行失败] command not found",
    "[工具调用失败] MCP disconnected",
    "参数不合法：limit 取 1-50",
])
async def test_普通成功文本不再按中文前缀猜测失败(text):
    class FakeSession:
        async def call_tool(self, _tool, _arguments):
            return SimpleNamespace(
                isError=False,
                content=[SimpleNamespace(text=text)],
            )

    mgr = ToolManager()
    mgr._sessions["fake"] = FakeSession()
    assert await mgr.call("fake", "tool", {}) == text


async def test_启动_列举_调用_关闭():
    mgr = ToolManager()
    await mgr.start()
    try:
        desc = mgr.describe()
        # 四个服务器的代表性工具都在清单里，且带风险标注
        for token in ("sysinfo.system_snapshot", "services.stop_service",
                      "logs.journal_search", "run_command.run_command",
                      "risk=high"):
            assert token in desc
        # 真实经 stdio 调一个工具：成功必须返回文本，底层命令不可用时
        # 必须显式抛错，不能再把失败字符串伪装成成功结果。
        try:
            out = await mgr.call("sysinfo", "top_processes",
                                 {"sort_by": "cpu", "limit": 3})
        except ToolCallError as exc:
            assert str(exc)
        else:
            assert isinstance(out, str) and out

        with pytest.raises(ToolCallError, match="参数不合法"):
            await mgr.call("sysinfo", "top_processes",
                           {"sort_by": "invalid", "limit": 3})
        with pytest.raises(ToolCallError, match="不在白名单"):
            await mgr.call("disk", "clean_file", {"path": "/etc/passwd"})
        with pytest.raises(ToolCallError, match="exit_code=127"):
            await mgr.call("run_command", "run_command",
                           {"command": "kylinguard-no-such-command-xyz"})
        with pytest.raises(ToolCallError):
            await mgr.call("services", "service_status",
                           {"name": "kylinguard-no-such.service"})
    finally:
        await mgr.stop()
