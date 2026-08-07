#!/usr/bin/env python3
"""扫描 backend.log 中 DCP / FanOut / ai_reverse 相关指标。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "logs" / "backend.log"

PATTERNS = {
    "dcp_block": re.compile(r"\[DCP\] BLOCK"),
    "dcp_allow": re.compile(r"\[DCP\] ALLOW"),
    "weak_oppose_fanout": re.compile(r"\[FanOut\].*weak_oppose"),
    "weak_oppose_skip": re.compile(r"skip\(温和反向"),
    "ai_reverse": re.compile(r"ai_reverse|close_and_open"),
    "pace_symmetric": re.compile(r"Pace 对称禁开|pace_symmetric_block"),
    "training_block": re.compile(r"训练期禁开|training_universe_block"),
}


def main() -> int:
    if not LOG.is_file():
        print(f"日志不存在: {LOG}")
        return 1
    text = LOG.read_text(encoding="utf-8", errors="replace")
    tail = text[-500_000:] if len(text) > 500_000 else text
    print(f"扫描 {LOG} (尾部 {len(tail)} 字符)\n")
    for name, pat in PATTERNS.items():
        count = len(pat.findall(tail))
        print(f"  {name}: {count}")
    if PATTERNS["weak_oppose_fanout"].search(tail):
        print("\n⚠ 仍有 weak_oppose 扇出成交日志，请检查 FanOut 修改是否生效")
        return 2
    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
