"""把 one-folder 产物打成交付 ZIP。

交付标准是三个产物（见 AGENTS.md「交付习惯」）：
  1. dist/local-v<版本>/简单ERP/        绿色版目录
  2. dist/simple-erp-setup-<版本>.exe   安装包
  3. dist/simple-erp-windows-<版本>.zip 绿色版 ZIP

本脚本负责第 3 个。刻意用 Python 而不是第三方打包器：既能精确控制
顶层目录名与条目集合，也能在写之前就拒绝业务数据混入。

用法：
    python packaging/make_release_zip.py [--version vX.Y.Z] [--dist-dir dist]
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import sys
import zipfile

# 绝不允许进入交付包的条目（真实业务数据 / 运行产物 / 演示库）
FORBIDDEN_SUFFIXES = (".db", ".db-wal", ".db-shm", ".log")
FORBIDDEN_PARTS = ("demo_data", "backups", "imports", "temp_pdf", "legacy_archive")
TOP_LEVEL_DIRNAME = "简单ERP"


def _read_version(repo_root: pathlib.Path, override: str | None) -> str:
    if override:
        version = override.strip()
    else:
        version = (repo_root / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"v\d+\.\d+\.\d+", version):
        raise SystemExit(f"版本格式不合法（应为 vX.Y.Z）：{version!r}")
    return version


def _collect(source: pathlib.Path) -> list[pathlib.Path]:
    files = sorted(p for p in source.rglob("*") if p.is_file())
    if not files:
        raise SystemExit(f"程序目录为空，先构建 one-folder 产物：{source}")
    if not (source / "简单ERP.exe").exists():
        raise SystemExit(f"程序目录里没有 简单ERP.exe：{source}")
    offenders = [
        str(p.relative_to(source))
        for p in files
        if p.name.endswith(FORBIDDEN_SUFFIXES) or any(part in p.parts for part in FORBIDDEN_PARTS)
    ]
    if offenders:
        raise SystemExit("交付包中出现禁止的条目，已中止：\n  " + "\n  ".join(offenders[:20]))
    return files


def build_zip(source: pathlib.Path, target: pathlib.Path) -> dict:
    files = _collect(source)
    if target.exists():
        target.unlink()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files:
            archive.write(path, pathlib.Path(TOP_LEVEL_DIRNAME) / path.relative_to(source))
    with zipfile.ZipFile(target) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise SystemExit(f"ZIP 存在坏条目：{bad}")
        names = archive.namelist()
    return {
        "entries": len(names),
        "size": target.stat().st_size,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "top_levels": sorted({name.split("/")[0] for name in names}),
        "has_root_config": f"{TOP_LEVEL_DIRNAME}/config.json" in names,
        "has_version": f"{TOP_LEVEL_DIRNAME}/_internal/VERSION" in names,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成简单ERP 交付 ZIP")
    parser.add_argument("--version", help="覆盖版本号（默认读仓库根 VERSION）")
    parser.add_argument("--dist-dir", default="dist", help="dist 目录（默认 dist）")
    parser.add_argument("--source", help="one-folder 产物目录（默认按版本推断）")
    args = parser.parse_args(argv)

    repo_root = pathlib.Path(__file__).resolve().parents[1]
    version = _read_version(repo_root, args.version)
    dist_dir = (repo_root / args.dist_dir).resolve()
    source = pathlib.Path(args.source).resolve() if args.source else dist_dir / f"local-{version}" / TOP_LEVEL_DIRNAME
    target = dist_dir / f"simple-erp-windows-{version}.zip"

    info = build_zip(source, target)
    print(f"ZIP: {target}")
    print(f"  条目数: {info['entries']}")
    print(f"  顶层:   {info['top_levels']}")
    print(f"  根 config.json: {info['has_root_config']}  _internal/VERSION: {info['has_version']}")
    print(f"  大小:   {info['size']} B")
    print(f"  SHA256: {info['sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
