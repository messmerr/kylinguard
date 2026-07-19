"""构建不夹带第三方安装包的银河麒麟 V11 / LoongArch64 安装包。"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
DEPLOY = ROOT / "deploy"
DEFAULT_EPOCH = 1_735_689_600  # 2025-01-01T00:00:00Z
EXECUTABLES = {
    "install.sh",
    "uninstall.sh",
    "docs/verify-install.sh",
    "deploy/kylinguard-privileged",
}


def run(argv: list[str], *, cwd: Path = ROOT) -> None:
    print("+", " ".join(argv), flush=True)
    subprocess.run(argv, cwd=cwd, check=True)


def version() -> str:
    metadata = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    value = str(metadata["project"]["version"])
    if not value or any(ch not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ._+-" for ch in value):
        raise SystemExit(f"不安全的版本号：{value!r}")
    return value


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(package_root: Path) -> None:
    lines = []
    for path in sorted(package_root.rglob("*")):
        if not path.is_file() or path.name == "SHA256SUMS":
            continue
        relative = path.relative_to(package_root).as_posix()
        if "\n" in relative or "\r" in relative:
            raise SystemExit(f"文件名不能写入 SHA256SUMS：{relative!r}")
        lines.append(f"{sha256(path)}  {relative}")
    (package_root / "SHA256SUMS").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    relative = PurePosixPath(info.name)
    inside = PurePosixPath(*relative.parts[1:])
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = int(os.environ.get("SOURCE_DATE_EPOCH", DEFAULT_EPOCH))
    if info.isdir():
        info.mode = 0o755
    elif inside.as_posix() in EXECUTABLES:
        info.mode = 0o755
    else:
        info.mode = 0o644
    return info


def create_archive(package_root: Path, archive: Path) -> None:
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", DEFAULT_EPOCH))
    with archive.open("wb") as raw:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=raw,
            compresslevel=9,
            mtime=epoch,
        ) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as tar:
                tar.add(
                    package_root,
                    arcname=package_root.name,
                    recursive=True,
                    filter=tar_filter,
                )


def verify_archive(archive: Path, expected_root: str) -> None:
    required = {
        f"{expected_root}/install.sh",
        f"{expected_root}/uninstall.sh",
        f"{expected_root}/SHA256SUMS",
        f"{expected_root}/payload/frontend/index.html",
        f"{expected_root}/deploy/kylinguard.service",
        f"{expected_root}/deploy/kylinguard-privileged",
    }
    wheels = []
    names = set()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            pure = PurePosixPath(member.name)
            if pure.is_absolute() or ".." in pure.parts:
                raise SystemExit(f"归档包含不安全路径：{member.name}")
            if member.issym() or member.islnk():
                raise SystemExit(f"归档不应包含链接：{member.name}")
            names.add(member.name.rstrip("/"))
            if member.name.startswith(f"{expected_root}/payload/wheels/") and member.name.endswith(".whl"):
                wheels.append(member.name)
            if member.name in {
                f"{expected_root}/install.sh",
                f"{expected_root}/uninstall.sh",
                f"{expected_root}/deploy/kylinguard-privileged",
            } and member.mode != 0o755:
                raise SystemExit(f"归档执行位错误：{member.name}")
    missing = sorted(required - names)
    if missing:
        raise SystemExit(f"归档缺少文件：{missing}")
    if len(wheels) != 1:
        raise SystemExit(f"归档中 KylinGuard wheel 数量应为 1，实际为 {len(wheels)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "dist",
        help="输出目录（默认：dist）",
    )
    parser.add_argument(
        "--skip-frontend-build",
        action="store_true",
        help="复用已有 frontend/dist，仅用于已完成构建的 CI 阶段",
    )
    args = parser.parse_args()

    package_version = version()
    npm = shutil.which("npm")
    if not args.skip_frontend_build:
        if npm is None:
            raise SystemExit("缺少 npm，无法构建前端")
        run([npm, "ci"], cwd=FRONTEND)
        run([npm, "run", "build"], cwd=FRONTEND)
    if not (FRONTEND / "dist" / "index.html").is_file():
        raise SystemExit("frontend/dist/index.html 不存在")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    package_name = f"KylinGuard-{package_version}-KylinV11-LoongArch64"
    archive = output_dir / f"{package_name}.tar.gz"

    with tempfile.TemporaryDirectory(prefix="kylinguard-package-") as temporary:
        temp_root = Path(temporary)
        package_root = temp_root / package_name
        wheels = package_root / "payload" / "wheels"
        wheels.mkdir(parents=True)
        # setuptools 的 wheel 构建会在输入树旁生成 build/；复制到临时目录后
        # 构建，保证打包命令不污染源码工作区。
        backend_source = temp_root / "backend-source"
        shutil.copytree(
            BACKEND,
            backend_source,
            ignore=shutil.ignore_patterns(
                "__pycache__",
                "*.pyc",
                "*.egg-info",
                "build",
            ),
        )

        run([
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--wheel-dir",
            str(wheels),
            str(backend_source),
        ])
        built_wheels = list(wheels.glob("kylinguard-*.whl"))
        if len(built_wheels) != 1:
            raise SystemExit(f"wheel 数量应为 1，实际为 {len(built_wheels)}")

        shutil.copytree(FRONTEND / "dist", package_root / "payload" / "frontend")
        for name in (
            "kylinguard.service",
            "kylinguard.env",
            "sudoers-kylinguard",
            "kylinguard-privileged",
            "constraints-kylin-v11.txt",
        ):
            copy_file(DEPLOY / name, package_root / "deploy" / name)
        copy_file(ROOT / "install.sh", package_root / "install.sh")
        copy_file(ROOT / "uninstall.sh", package_root / "uninstall.sh")
        copy_file(
            DEPLOY / "verify-install.sh",
            package_root / "docs" / "verify-install.sh",
        )
        copy_file(
            ROOT / "docs" / "安装包与银河麒麟V11部署手册.md",
            package_root / "docs" / "安装包与银河麒麟V11部署手册.md",
        )

        (package_root / "VERSION").write_text(
            package_version + "\n",
            encoding="utf-8",
            newline="\n",
        )
        (package_root / "PACKAGE-METADATA.json").write_text(
            json.dumps(
                {
                    "name": "KylinGuard",
                    "version": package_version,
                    "target_os": "银河麒麟高级服务器操作系统 V11",
                    "target_arch": "LoongArch64",
                    "contains_third_party_install_packages": False,
                    "runtime_dependencies_installed_online": True,
                    "frontend_prebuilt": True,
                    "backend_format": "py3-none-any wheel",
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        build_manifest(package_root)
        if archive.exists():
            archive.unlink()
        create_archive(package_root, archive)

    verify_archive(archive, package_name)
    external_checksum = archive.with_suffix(archive.suffix + ".sha256")
    external_checksum.write_text(
        f"{sha256(archive)}  {archive.name}\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"安装包：{archive}")
    print(f"校验值：{external_checksum}")


if __name__ == "__main__":
    main()
