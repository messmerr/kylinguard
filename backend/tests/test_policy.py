import pytest

from kylinguard.models import RuleDecision
from kylinguard.policy import PolicyStore
from kylinguard.rules import ExtraPolicies, builtin_rules, check_command


@pytest.fixture()
def store(tmp_path):
    s = PolicyStore(str(tmp_path / "kg.db"))
    yield s
    s.close()


def test_增删查(store):
    pid = store.add("blacklist", r"\bwipefs\b", "擦除文件系统签名")
    items = store.list()
    assert items[0]["id"] == pid and items[0]["kind"] == "blacklist"
    store.remove(pid)
    assert store.list() == []


def test_非法kind与非法正则拒绝(store):
    with pytest.raises(ValueError):
        store.add("unknown_kind", "x", "")
    with pytest.raises(ValueError):
        store.add("blacklist", "([未闭合", "")
    with pytest.raises(ValueError, match="绝对路径"):
        store.add("protected", "relative/path", "")


def test_extra聚合(store):
    store.add("blacklist", r"\bwipefs\b", "擦除签名")
    store.add("readonly", "lsattr", "查看文件属性")
    store.add("protected", "/etc/kylin-release", "系统版本标识")
    extra = store.extra()
    assert (r"\bwipefs\b", "擦除签名") in extra.blacklist
    assert "lsattr" in extra.readonly
    assert "/etc/kylin-release" in extra.protected


def test_自定义黑名单参与判定(store):
    store.add("blacklist", r"\bwipefs\b", "擦除文件系统签名")
    v = check_command("wipefs -a /dev/sdb1", extra=store.extra())
    assert v.decision == RuleDecision.DENY
    assert "wipefs" in (v.matched_rule or "")


def test_自定义白名单参与判定(store):
    assert check_command("lsattr /var/log").decision == RuleDecision.REVIEW
    store.add("readonly", "lsattr", "")
    assert check_command("lsattr /var/log",
                         extra=store.extra()).decision == RuleDecision.REVIEW


@pytest.mark.parametrize("command", [
    "git reset --hard",
    "git push --force origin main",
    "find /tmp -delete",
])
def test_自定义命令名不能把危险参数自动标为只读(store, command):
    store.add("readonly", command.split()[0], "旧版可信命令")
    assert check_command(command, extra=store.extra()).decision != RuleDecision.ALLOW


def test_自定义保护路径参与判定(store):
    store.add("protected", "/etc/kylin-release", "")
    v = check_command("rm /etc/kylin-release", extra=store.extra())
    assert v.decision == RuleDecision.DENY


def test_extra为None行为不变():
    assert check_command("ps aux", extra=None).decision == RuleDecision.ALLOW
    assert check_command("rm -rf /", extra=None).decision == RuleDecision.DENY


def test_builtin_rules导出完整():
    b = builtin_rules()
    assert any("dd" in p for p, _ in b["blacklist"])
    assert "sudo" in b["privilege_escalators"]
    assert "/etc/shadow" in b["protected_prefixes"]
    assert "ps" in b["safe_commands"]
    assert "status" in b["systemctl_ro_subcmds"]


def test_策略快照修订号仅在真实变更时递增(tmp_path):
    store = PolicyStore(str(tmp_path / "revision.db"))
    revision0, extra0 = store.snapshot()
    policy_id = store.add("protected", str(tmp_path / "protected"), "")
    revision1, extra1 = store.snapshot()

    assert revision1 == revision0 + 1
    assert extra0.protected == ()
    assert extra1.protected
    assert store.remove(999999) is False
    assert store.revision() == revision1
    assert store.remove(policy_id) is True
    assert store.revision() == revision1 + 1
    store.close()
