"""按 requirements.txt 内容哈希决定是否重装依赖。

每次启动都跑 pip install 会联网查一遍索引，即使依赖早就齐了也要等十几秒。
这里把 requirements.txt 的 sha256 记在 venv 里，内容没变就直接跳过。
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQ = ROOT / "requirements.txt"
STAMP = Path(sys.prefix) / ".requirements.sha256"


def main() -> int:
    if not REQ.is_file():
        print(f"找不到 {REQ}")
        return 1
    digest = hashlib.sha256(REQ.read_bytes()).hexdigest()
    if STAMP.is_file() and STAMP.read_text(encoding="utf-8").strip() == digest:
        print("依赖未变化，跳过安装。")
        return 0

    print("依赖有变化，正在安装 ...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(REQ)])
    STAMP.write_text(digest, encoding="utf-8")
    print("依赖安装完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
