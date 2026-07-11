import hashlib
import json
import os
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from kylinguard.plugins import files
from kylinguard.subprocess_env import safe_subprocess_env


def _json(result: str) -> dict:
    return json.loads(result)


def test_write_read_and_list_return_safe_summaries(tmp_path: Path):
    target = tmp_path / "记录.md"
    secret = "不要在写入结果里回显的正文 secret-value"

    written = _json(files.write_file(
        path=str(target), content=secret, create_only=True))
    assert written == {
        "operation": "created",
        "path": str(target),
        "bytes": len(secret.encode()),
        "sha256": hashlib.sha256(secret.encode()).hexdigest(),
    }
    assert secret not in json.dumps(written, ensure_ascii=False)
    assert not list(tmp_path.glob(".*.kylinguard-*.tmp"))

    read = files.read_file(path=str(target))
    assert f"sha256={written['sha256']}" in read
    assert read.endswith(secret)

    listing = _json(files.list_directory(path=str(tmp_path), limit=10))
    assert listing["entries"] == [
        {"name": "记录.md", "type": "file", "bytes": len(secret.encode())}
    ]
    assert listing["truncated"] is False


def test_all_paths_must_be_absolute():
    with pytest.raises(ToolError, match="必须是绝对路径"):
        files.read_file(path="relative/note.md")
    with pytest.raises(ToolError, match="必须是绝对路径"):
        files.write_file(path="relative/note.md", content="x")


def test_write_has_size_limit(tmp_path: Path):
    with pytest.raises(ToolError, match="文本过大"):
        files.write_file(
            path=str(tmp_path / "large.txt"),
            content="a" * (files.MAX_TEXT_BYTES + 1),
        )
    assert not (tmp_path / "large.txt").exists()


def test_create_only_and_expected_hash_protect_existing_file(tmp_path: Path):
    target = tmp_path / "note.txt"
    target.write_text("v1", encoding="utf-8")
    v1_hash = hashlib.sha256(b"v1").hexdigest()

    with pytest.raises(ToolError, match="create_only"):
        files.write_file(path=str(target), content="lost", create_only=True)
    with pytest.raises(ToolError, match="expected_sha256 不匹配"):
        files.write_file(
            path=str(target), content="lost", expected_sha256="0" * 64)
    assert target.read_text(encoding="utf-8") == "v1"

    changed = _json(files.write_file(
        path=str(target), content="v2", expected_sha256=v1_hash))
    assert changed["operation"] == "replaced"
    assert target.read_text(encoding="utf-8") == "v2"


def test_invalid_expected_hash_rejected_before_mutation(tmp_path: Path):
    target = tmp_path / "note.txt"
    target.write_text("stable", encoding="utf-8")
    with pytest.raises(ToolError, match="64 位十六进制"):
        files.write_file(
            path=str(target), content="changed", expected_sha256="not-a-hash")
    assert target.read_text(encoding="utf-8") == "stable"


def test_replace_text_is_precise_and_hash_bound(tmp_path: Path):
    target = tmp_path / "note.txt"
    target.write_text("same / same", encoding="utf-8")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()

    with pytest.raises(ToolError, match="共出现 2 次"):
        files.replace_text(path=str(target), old_text="same", new_text="new")
    assert target.read_text(encoding="utf-8") == "same / same"

    result = _json(files.replace_text(
        path=str(target), old_text="same", new_text="new",
        expected_sha256=digest, replace_all=True,
    ))
    assert result["operation"] == "replace_text"
    assert result["replacements"] == 2
    assert "same" not in json.dumps(result)
    assert target.read_text(encoding="utf-8") == "new / new"


def test_mkdir_move_delete_lifecycle(tmp_path: Path):
    directory = tmp_path / "notes" / "daily"
    made = _json(files.mkdir(path=str(directory), parents=True))
    assert made["type"] == "directory"

    source = directory / "a.md"
    destination = directory / "b.md"
    files.write_file(path=str(source), content="hello", create_only=True)
    moved = _json(files.move(source=str(source), destination=str(destination)))
    assert moved["operation"] == "move"
    assert not source.exists() and destination.read_text() == "hello"

    digest = hashlib.sha256(b"hello").hexdigest()
    deleted = _json(files.delete(
        path=str(destination), expected_sha256=digest))
    assert deleted["operation"] == "delete"
    assert deleted["sha256"] == digest
    assert not destination.exists()

    files.delete(path=str(directory))
    files.delete(path=str(directory.parent))


def test_move_overwrite_requires_destination_hash(tmp_path: Path):
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("new", encoding="utf-8")
    destination.write_text("old", encoding="utf-8")

    with pytest.raises(ToolError, match="expected_destination_sha256"):
        files.move(
            source=str(source), destination=str(destination), overwrite=True)
    assert source.read_text() == "new" and destination.read_text() == "old"

    old_hash = hashlib.sha256(b"old").hexdigest()
    files.move(
        source=str(source), destination=str(destination), overwrite=True,
        expected_destination_sha256=old_hash,
    )
    assert not source.exists() and destination.read_text() == "new"


@pytest.mark.skipif(os.name == "nt", reason="Windows 普通用户默认不能创建符号链接")
def test_symlink_components_cannot_escape(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("unchanged", encoding="utf-8")
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ToolError, match="符号链接"):
        files.read_file(path=str(allowed / "escape" / "secret.txt"))
    with pytest.raises(ToolError, match="符号链接"):
        files.write_file(
            path=str(allowed / "escape" / "secret.txt"), content="changed")
    assert (outside / "secret.txt").read_text() == "unchanged"


@pytest.mark.skipif(os.name == "nt", reason="Windows 普通用户默认不能创建符号链接")
def test_recursive_delete_preflights_symlinks_without_partial_delete(tmp_path: Path):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "ordinary.txt").write_text("keep", encoding="utf-8")
    (tree / "link").symlink_to(tmp_path / "missing")

    with pytest.raises(ToolError, match="存在符号链接"):
        files.delete(path=str(tree), recursive=True)
    assert (tree / "ordinary.txt").read_text() == "keep"
    assert (tree / "link").is_symlink()


def _swap_directory_for_symlink(directory: Path, outside: Path) -> Path:
    pinned = directory.with_name(f"{directory.name}-pinned")
    directory.rename(pinned)
    directory.symlink_to(outside, target_is_directory=True)
    return pinned


@pytest.mark.skipif(os.name != "posix", reason="dirfd/O_NOFOLLOW 加固面向 Linux/WSL")
def test_read_stays_on_opened_parent_when_path_is_swapped_to_symlink(
    tmp_path: Path, monkeypatch,
):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    (allowed / "note.txt").write_text("inside", encoding="utf-8")
    (outside / "note.txt").write_text("outside", encoding="utf-8")

    real_open = files._open_bound_entry
    pinned = None

    def swap_then_open(parent_fd, name, display):
        nonlocal pinned
        if pinned is None:
            pinned = _swap_directory_for_symlink(allowed, outside)
        return real_open(parent_fd, name, display)

    monkeypatch.setattr(files, "_open_bound_entry", swap_then_open)
    result = files.read_file(path=str(allowed / "note.txt"))

    assert result.endswith("inside")
    assert (outside / "note.txt").read_text(encoding="utf-8") == "outside"
    assert (pinned / "note.txt").read_text(encoding="utf-8") == "inside"


@pytest.mark.skipif(os.name != "posix", reason="dirfd/O_NOFOLLOW 加固面向 Linux/WSL")
def test_list_directory_fails_closed_if_final_component_becomes_symlink(
    tmp_path: Path, monkeypatch,
):
    victim = tmp_path / "victim"
    outside = tmp_path / "outside"
    victim.mkdir()
    outside.mkdir()
    (victim / "inside.txt").write_text("inside", encoding="utf-8")
    (outside / "secret.txt").write_text("outside", encoding="utf-8")

    real_open_dir = files._open_dir_at
    swapped = False

    def swap_then_open(parent_fd, name, label):
        nonlocal swapped
        if not swapped and Path(label) == victim:
            swapped = True
            _swap_directory_for_symlink(victim, outside)
        return real_open_dir(parent_fd, name, label)

    monkeypatch.setattr(files, "_open_dir_at", swap_then_open)
    with pytest.raises(ToolError, match="符号链接"):
        files.list_directory(path=str(victim))
    assert (outside / "secret.txt").read_text(encoding="utf-8") == "outside"


@pytest.mark.skipif(os.name != "posix", reason="dirfd/O_NOFOLLOW 加固面向 Linux/WSL")
def test_mkdir_uses_opened_parent_fd_during_symlink_swap(
    tmp_path: Path, monkeypatch,
):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    real_mkdir = files.os.mkdir
    pinned = None

    def swap_then_mkdir(name, mode=0o777, *, dir_fd=None):
        nonlocal pinned
        if pinned is None and name == "child":
            pinned = _swap_directory_for_symlink(allowed, outside)
        return real_mkdir(name, mode=mode, dir_fd=dir_fd)

    monkeypatch.setattr(files.os, "mkdir", swap_then_mkdir)
    files.mkdir(path=str(allowed / "child"))

    assert (pinned / "child").is_dir()
    assert not (outside / "child").exists()


@pytest.mark.skipif(os.name != "posix", reason="dirfd/O_NOFOLLOW 加固面向 Linux/WSL")
def test_write_stays_on_opened_parent_when_path_is_swapped_to_symlink(
    tmp_path: Path, monkeypatch,
):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    (allowed / "note.txt").write_text("old-inside", encoding="utf-8")
    (outside / "note.txt").write_text("outside", encoding="utf-8")

    real_create = files._create_temporary
    pinned = None

    def swap_then_create(parent_fd, mode):
        nonlocal pinned
        if pinned is None:
            pinned = _swap_directory_for_symlink(allowed, outside)
        return real_create(parent_fd, mode)

    monkeypatch.setattr(files, "_create_temporary", swap_then_create)
    files.write_file(path=str(allowed / "note.txt"), content="new-inside")

    assert (outside / "note.txt").read_text(encoding="utf-8") == "outside"
    assert (pinned / "note.txt").read_text(encoding="utf-8") == "new-inside"


@pytest.mark.skipif(os.name != "posix", reason="dirfd/O_NOFOLLOW 加固面向 Linux/WSL")
def test_replace_text_reuses_the_parent_fd_opened_for_read(
    tmp_path: Path, monkeypatch,
):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    (allowed / "note.txt").write_text("old inside", encoding="utf-8")
    # 内容刻意相同；若 replace_text 在读取后重新解析完整路径，哈希检查也
    # 无法阻止它修改被换入路径的文件。
    (outside / "note.txt").write_text("old inside", encoding="utf-8")

    real_write_at = files._atomic_write_at
    pinned = None

    def swap_then_write_at(path, parent_fd, name, data, **kwargs):
        nonlocal pinned
        if pinned is None:
            pinned = _swap_directory_for_symlink(allowed, outside)
        return real_write_at(path, parent_fd, name, data, **kwargs)

    monkeypatch.setattr(files, "_atomic_write_at", swap_then_write_at)
    files.replace_text(
        path=str(allowed / "note.txt"), old_text="old", new_text="new")

    assert (outside / "note.txt").read_text(encoding="utf-8") == "old inside"
    assert (pinned / "note.txt").read_text(encoding="utf-8") == "new inside"


@pytest.mark.skipif(os.name != "posix", reason="dirfd/O_NOFOLLOW 加固面向 Linux/WSL")
def test_write_rejects_target_inode_swap_without_overwriting_replacement(
    tmp_path: Path, monkeypatch,
):
    target = tmp_path / "note.txt"
    original = tmp_path / "original.txt"
    target.write_text("original", encoding="utf-8")
    real_create = files._create_temporary
    swapped = False

    def swap_target_then_create(parent_fd, mode):
        nonlocal swapped
        if not swapped:
            swapped = True
            target.rename(original)
            target.write_text("attacker replacement", encoding="utf-8")
        return real_create(parent_fd, mode)

    monkeypatch.setattr(files, "_create_temporary", swap_target_then_create)
    with pytest.raises(ToolError, match="发生变化"):
        files.write_file(path=str(target), content="new content")

    assert original.read_text(encoding="utf-8") == "original"
    assert target.read_text(encoding="utf-8") == "attacker replacement"
    assert not list(tmp_path.glob(".kylinguard-*"))


@pytest.mark.skipif(os.name != "posix", reason="dirfd/O_NOFOLLOW 加固面向 Linux/WSL")
def test_move_stays_on_opened_directories_during_symlink_swap(
    tmp_path: Path, monkeypatch,
):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    (allowed / "source.txt").write_text("inside", encoding="utf-8")
    (outside / "destination.txt").write_text("outside", encoding="utf-8")

    real_rename = files._rename_noreplace
    pinned = None

    def swap_then_rename(source_fd, source, destination_fd, destination):
        nonlocal pinned
        if pinned is None:
            pinned = _swap_directory_for_symlink(allowed, outside)
        return real_rename(source_fd, source, destination_fd, destination)

    monkeypatch.setattr(files, "_rename_noreplace", swap_then_rename)
    files.move(
        source=str(allowed / "source.txt"),
        destination=str(allowed / "destination.txt"),
    )

    assert (outside / "destination.txt").read_text(encoding="utf-8") == "outside"
    assert not (pinned / "source.txt").exists()
    assert (pinned / "destination.txt").read_text(encoding="utf-8") == "inside"


@pytest.mark.skipif(os.name != "posix", reason="dirfd/O_NOFOLLOW 加固面向 Linux/WSL")
def test_move_detects_source_inode_swap_and_rolls_back_unvalidated_entry(
    tmp_path: Path, monkeypatch,
):
    source = tmp_path / "source.txt"
    original = tmp_path / "original.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("original", encoding="utf-8")
    real_rename = files._rename_noreplace
    swapped = False

    def swap_source_then_rename(source_fd, source_name, destination_fd, destination_name):
        nonlocal swapped
        if not swapped and source_name == source.name:
            swapped = True
            source.rename(original)
            source.write_text("attacker replacement", encoding="utf-8")
        return real_rename(source_fd, source_name, destination_fd, destination_name)

    monkeypatch.setattr(files, "_rename_noreplace", swap_source_then_rename)
    with pytest.raises(ToolError, match="并发替换"):
        files.move(source=str(source), destination=str(destination))

    assert original.read_text(encoding="utf-8") == "original"
    assert source.read_text(encoding="utf-8") == "attacker replacement"
    assert not destination.exists()


@pytest.mark.skipif(os.name != "posix", reason="dirfd/O_NOFOLLOW 加固面向 Linux/WSL")
def test_delete_stays_on_opened_parent_during_symlink_swap(
    tmp_path: Path, monkeypatch,
):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    (allowed / "note.txt").write_text("inside", encoding="utf-8")
    (outside / "note.txt").write_text("outside", encoding="utf-8")

    real_quarantine = files._quarantine_entry
    pinned = None

    def swap_then_quarantine(parent_fd, name, expected, display):
        nonlocal pinned
        if pinned is None:
            pinned = _swap_directory_for_symlink(allowed, outside)
        return real_quarantine(parent_fd, name, expected, display)

    monkeypatch.setattr(files, "_quarantine_entry", swap_then_quarantine)
    files.delete(path=str(allowed / "note.txt"))

    assert (outside / "note.txt").read_text(encoding="utf-8") == "outside"
    assert not (pinned / "note.txt").exists()


@pytest.mark.skipif(os.name != "posix", reason="dirfd/O_NOFOLLOW 加固面向 Linux/WSL")
def test_delete_rejects_target_inode_swap_without_deleting_replacement(
    tmp_path: Path, monkeypatch,
):
    target = tmp_path / "note.txt"
    original = tmp_path / "original.txt"
    target.write_text("original", encoding="utf-8")
    real_quarantine = files._quarantine_entry
    swapped = False

    def swap_target_then_quarantine(parent_fd, name, expected, display):
        nonlocal swapped
        if not swapped:
            swapped = True
            target.rename(original)
            target.write_text("attacker replacement", encoding="utf-8")
        return real_quarantine(parent_fd, name, expected, display)

    monkeypatch.setattr(files, "_quarantine_entry", swap_target_then_quarantine)
    with pytest.raises(ToolError, match="发生变化"):
        files.delete(path=str(target))

    assert original.read_text(encoding="utf-8") == "original"
    assert target.read_text(encoding="utf-8") == "attacker replacement"


@pytest.mark.skipif(os.name != "posix", reason="dirfd/O_NOFOLLOW 加固面向 Linux/WSL")
def test_recursive_delete_stays_on_bound_tree_during_parent_symlink_swap(
    tmp_path: Path, monkeypatch,
):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    tree = allowed / "tree"
    tree.mkdir()
    (tree / "inside.txt").write_text("inside", encoding="utf-8")
    outside_tree = outside / "tree"
    outside_tree.mkdir()
    (outside_tree / "outside.txt").write_text("outside", encoding="utf-8")

    real_quarantine = files._quarantine_entry
    pinned = None

    def swap_top_then_quarantine(parent_fd, name, expected, display):
        nonlocal pinned
        if pinned is None:
            pinned = _swap_directory_for_symlink(allowed, outside)
        return real_quarantine(parent_fd, name, expected, display)

    monkeypatch.setattr(files, "_quarantine_entry", swap_top_then_quarantine)
    files.delete(path=str(tree), recursive=True)

    assert (outside_tree / "outside.txt").read_text(encoding="utf-8") == "outside"
    assert not (pinned / "tree").exists()


@pytest.mark.skipif(os.name != "posix", reason="dirfd/O_NOFOLLOW 加固面向 Linux/WSL")
def test_final_symlink_is_rejected_for_move_and_delete(tmp_path: Path):
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(outside)

    with pytest.raises(ToolError, match="符号链接"):
        files.move(source=str(link), destination=str(tmp_path / "moved.txt"))
    with pytest.raises(ToolError, match="符号链接"):
        files.delete(path=str(link))
    assert outside.read_text(encoding="utf-8") == "keep"


def test_subprocess_environment_uses_allowlist():
    source = {
        "PATH": "/usr/bin",
        "HOME": "/home/runner",
        "LANG": "zh_CN.UTF-8",
        "TMPDIR": "/tmp",
        "KG_LLM_API_KEY": "llm-secret",
        "KG_ADMIN_PASSWORD": "admin-secret",
        "OPENAI_API_KEY": "openai-secret",
        "HTTPS_PROXY": "http://user:password@proxy",
        "SSH_AUTH_SOCK": "/tmp/agent.sock",
        "AWS_SECRET_ACCESS_KEY": "cloud-secret",
    }
    env = safe_subprocess_env(source)
    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/home/runner"
    assert env["LANG"] == "zh_CN.UTF-8"
    assert env["TMPDIR"] == "/tmp"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert not any("secret" in value for value in env.values())
    assert "HTTPS_PROXY" not in env
    assert "SSH_AUTH_SOCK" not in env
