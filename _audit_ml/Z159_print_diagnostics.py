# -*- coding: utf-8 -*-
"""Z159 (目标项 (f)): 生产代码里用 print() 报错/告警的地方 —— 这些信息进不了 logging/审计流。

判定口径：
  * 只扫 backend/services/**、backend/api/**（生产代码），排除 tests/scripts；
  * 标注「错误语义」行（含 Error/fail/异常/跳过/blocked/拦截/warning）
    与「计数/进度」行；
  * 输出每个文件的命中数，便于按影响面取前 N 个修。
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena\backend")
SKIP = ("_pytest_tmp", "__pycache__", "_ai_gen_quarantine")
SCAN_DIRS = [ROOT / "services", ROOT / "api"]


def main() -> None:
    per_file: Counter[str] = Counter()
    err_lines: list[str] = []
    other_lines: list[str] = []
    prints = re.compile(r"(?<![\w.])print\s*\(")
    errish = re.compile(r"(error|fail|except|异常|失败|跳过|skip|blocked|拦截|warn|缺失|不可用|超时)", re.I)
    for d in SCAN_DIRS:
        for py in sorted(d.rglob("*.py")):
            rel = str(py.relative_to(ROOT.parent))
            if any(s in rel for s in SKIP) or "/tests/" in rel or "\\tests\\" in rel:
                continue
            for i, line in enumerate(py.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if not prints.search(line):
                    continue
                per_file[rel] += 1
                rec = f"{rel}:{i}: {line.strip()[:120]}"
                (err_lines if errish.search(line) else other_lines).append(rec)

    print(f"生产代码中 print() 命中文件数 {len(per_file)}，总行数 {sum(per_file.values())}")
    print(f"  其中「错误语义」行: {len(err_lines)}")
    print(f"  其中「普通输出」行: {len(other_lines)}")
    print("\n=== 命中最多的文件 ===")
    for rel, n in per_file.most_common(20):
        print(f"  {n:4d}  {rel}")
    print("\n=== 错误语义的 print（前 40 条，优先修） ===")
    for r in err_lines[:40]:
        print("  " + r)


if __name__ == "__main__":
    main()
