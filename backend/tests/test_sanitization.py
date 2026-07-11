from kylinguard.models import PlanStep, RiskLevel
from kylinguard.sanitization import (
    canonical_fingerprint,
    redact_text,
    safe_step,
)


def test_redact_text清理常见凭据但保留变量名():
    text = "KG_LLM_API_KEY=sk-abcdefghijklmnop Authorization: Bearer abc.def"
    cleaned = redact_text(text)
    assert "abcdefghijklmnop" not in cleaned
    assert "abc.def" not in cleaned
    assert "KG_LLM_API_KEY=[REDACTED]" in cleaned


def test_file正文进入审计时只保留摘要():
    step = PlanStep(
        tool="files.write_file",
        arguments={"path": "/tmp/note.md", "content": "机密正文"},
        purpose="记录信息",
        risk=RiskLevel.MEDIUM,
    )
    cleaned = safe_step(step)
    summary = cleaned["arguments"]["content"]
    assert "机密正文" not in str(cleaned)
    assert summary["redacted"] is True
    assert summary["bytes"] == len("机密正文".encode())
    assert len(summary["sha256"]) == 64


def test_action_fingerprint与字典插入顺序无关():
    left = canonical_fingerprint({"tool": "x", "arguments": {"a": 1, "b": 2}})
    right = canonical_fingerprint({"arguments": {"b": 2, "a": 1}, "tool": "x"})
    assert left == right
