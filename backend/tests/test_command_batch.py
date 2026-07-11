import pytest

from kylinguard.command_batch import CommandSyntaxError, parse_simple_batch


def test_只读分号命令拆成独立argv():
    batch = parse_simple_batch("ps aux; free -m")
    assert batch.commands == [["ps", "aux"], ["free", "-m"]]
    assert batch.operators == [";"]


def test_支持条件连接但不交给shell():
    batch = parse_simple_batch("test -f '/tmp/a b' && stat '/tmp/a b' || ls /tmp")
    assert batch.commands == [
        ["test", "-f", "/tmp/a b"],
        ["stat", "/tmp/a b"],
        ["ls", "/tmp"],
    ]
    assert batch.operators == ["&&", "||"]


def test_引号内分号不是连接符():
    batch = parse_simple_batch("echo ';'")
    assert batch.commands == [["echo", ";"]]
    assert batch.operators == []


@pytest.mark.parametrize("command", [
    "ps aux | mail x@example.com",
    "echo x > /tmp/f",
    "sleep 1 &",
    "echo $(id)",
    "echo `id`",
    "ps aux;",
    "; ps aux",
    "echo 'unclosed",
])
def test_不支持的shell语义给出可改写错误(command):
    with pytest.raises(CommandSyntaxError):
        parse_simple_batch(command)

