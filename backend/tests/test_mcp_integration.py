import pytest

from kylinguard.mcp_client import ToolManager, split_qualified


def test_限定名拆分():
    assert split_qualified("sysinfo.top_processes") == ("sysinfo", "top_processes")
    with pytest.raises(ValueError):
        split_qualified("裸名无点号")


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
        # 真实经 stdio 调一个工具：无论底层命令在本机是否可用，
        # 都应返回字符串（失败时是"[执行失败]/参数不合法"这类降级文本）
        out = await mgr.call("sysinfo", "top_processes",
                             {"sort_by": "cpu", "limit": 3})
        assert isinstance(out, str) and out
    finally:
        await mgr.stop()
