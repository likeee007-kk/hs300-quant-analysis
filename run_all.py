# -*- coding: utf-8 -*-
"""一键按顺序运行 01–04；任何一步失败立即退出。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STEPS = [
    "01_fetch_data.py",
    "02_align_pmi.py",
    "03_clean_and_features.py",
    "04_backtest.py",
]


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def main() -> int:
    _force_utf8()
    for step in STEPS:
        print(f"\n=== {step} ===")
        subprocess.run([sys.executable, str(ROOT / step)], cwd=ROOT, check=True)
    print("\n所有步骤完成：data/ 与 figures/ 已更新。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
