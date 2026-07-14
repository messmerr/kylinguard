import pytest

from kylinguard.context_files import (
    ContextFileError,
    ContextMentionError,
    normalize_context_mentions,
    search_context_files,
    validate_context_files,
)


def test_context_files只接受工作区内普通文件并返回元数据(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    target = docs / "说明.md"
    target.write_text("不会被接口读取的正文", encoding="utf-8")

    result = validate_context_files(tmp_path, ["docs/说明.md"])

    assert result == [{
        "relative_path": "docs/说明.md",
        "name": "说明.md",
        "size": target.stat().st_size,
    }]
    assert "正文" not in repr(result)


@pytest.mark.parametrize("path", [
    "../outside.txt", "/etc/passwd", ".env", ".git/config",
])
def test_context_files拒绝逃逸绝对路径和隐藏敏感路径(tmp_path, path):
    with pytest.raises(ContextFileError):
        validate_context_files(tmp_path, [path])


def test_context_files拒绝符号链接和目录(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(outside)
    directory = tmp_path / "folder"
    directory.mkdir()

    with pytest.raises(ContextFileError, match="符号链接"):
        validate_context_files(tmp_path, ["link.txt"])
    with pytest.raises(ContextFileError, match="不是普通文件"):
        validate_context_files(tmp_path, ["folder"])


def test_context_files拒绝中间目录符号链接(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside-dir"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (tmp_path / "linked-dir").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ContextFileError, match="符号链接"):
        validate_context_files(tmp_path, ["linked-dir/secret.txt"])


def test_context_file_search跳过重目录隐藏项和符号链接(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "disk-report.md").write_text("body", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "disk-secret").write_text("secret", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "disk-package.js").write_text("x", encoding="utf-8")
    (tmp_path / "escape").symlink_to(tmp_path.parent, target_is_directory=True)

    result = search_context_files(tmp_path, "disk")

    assert [item["relative_path"] for item in result["files"]] == [
        "notes/disk-report.md"
    ]
    assert all("content" not in item for item in result["files"])
    assert result["scanned"] > 0


def test_context_files最多八项(tmp_path):
    paths = []
    for index in range(9):
        name = f"{index}.txt"
        (tmp_path / name).write_text(str(index), encoding="utf-8")
        paths.append(name)

    with pytest.raises(ContextFileError, match="最多引用 8"):
        validate_context_files(tmp_path, paths)


def test_context_mentions按unicode位置排序且只使用服务端名称():
    message = "😀@配置复核 请检查 @app.yaml"
    file_offset = message.index("@app.yaml")
    normalized = normalize_context_mentions(
        message,
        [
            {
                "type": "file", "offset": file_offset,
                "path": "config/app.yaml", "name": "伪造文件名",
            },
            {
                "type": "skill", "offset": 1,
                "skill_id": "config-review", "name": "伪造 Skill 名",
            },
        ],
        skill_names={"config-review": "配置复核"},
        context_files=[{
            "relative_path": "config/app.yaml",
            "name": "app.yaml",
            "size": 12,
        }],
    )

    assert normalized == [
        {
            "type": "skill", "offset": 1,
            "skill_id": "config-review", "name": "配置复核",
        },
        {
            "type": "file", "offset": file_offset,
            "path": "config/app.yaml", "name": "app.yaml",
        },
    ]


@pytest.mark.parametrize("mention", [
    {"type": "skill", "offset": 0, "skill_id": "not-selected"},
    {"type": "file", "offset": 0, "path": "other.txt"},
    {"type": "file", "offset": 99, "path": "config/app.yaml"},
])
def test_context_mentions拒绝越界位置和未选择引用(mention):
    with pytest.raises(ContextMentionError):
        normalize_context_mentions(
            "检查",
            [mention],
            skill_names={"config-review": "配置复核"},
            context_files=[{
                "relative_path": "config/app.yaml",
                "name": "app.yaml",
                "size": 12,
            }],
        )


def test_context_mentions必须精确覆盖可信token且不能重叠():
    with pytest.raises(ContextMentionError, match="精确指向"):
        normalize_context_mentions(
            "@配置检查",
            [{"type": "skill", "offset": 0, "skill_id": "review"}],
            skill_names={"review": "配置复核"},
            context_files=[],
        )

    with pytest.raises(ContextMentionError, match="不能重叠"):
        normalize_context_mentions(
            "@配置复核",
            [
                {"type": "skill", "offset": 0, "skill_id": "short"},
                {"type": "file", "offset": 0, "path": "配置复核"},
            ],
            skill_names={"short": "配置"},
            context_files=[{
                "relative_path": "配置复核",
                "name": "配置复核",
                "size": 0,
            }],
        )
