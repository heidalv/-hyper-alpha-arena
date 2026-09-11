# -*- coding: utf-8 -*-
"""[§90 缺陷 #73 修复] 给审计脚本统一加**账户隔离**（默认 account_id=14）。

做法（保守、可核对）：对目标脚本
  1. 插入 `sys.path` 下的 `_audit_ml` 并 `from _scope import account_clause, describe_scope`；
  2. 把 SQL 里的 `where status='closed'` 改成带 `{ACCT}` 占位的形式，
     并在 SQL 前定义 `ACCT = account_clause()`（f-string 已存在时直接内联）；
  3. 在输出头部打印 `describe_scope()`，让每次运行的口径可见。
逐文件打印改动条数，便于复核。
"""
from __future__ import annotations

import re
import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
TARGETS = ["Z221_fix_effect_slice.py", "Z251_peak_transition_analysis.py",
           "Z252_long_loss_mechanism.py", "Z254_long_sl_chain_audit.py",
           "Z259_entry_profile.py", "Z260_p29_recheck.py"]


def patch(path: Path) -> int:
    src = path.read_text(encoding="utf-8")
    if "_scope" in src:
        print(f"  {path.name}: 已打过补丁，跳过")
        return 0
    orig = src
    # 1) import：插在 load_dotenv 之后
    anchor = "load_dotenv(str(ROOT / \".env\"), override=False)\n"
    imp = (anchor
           + "sys.path.insert(0, str(Path(__file__).resolve().parent))\n"
             "from _scope import account_clause, describe_scope  # noqa: E402\n\n"
             "ACCT = account_clause()   # [§90/#73] 账户隔离（默认 account_id=14，AUDIT_ACCOUNT_ID=0 关闭）\n")
    if anchor in src:
        src = src.replace(anchor, imp, 1)
    else:
        print(f"  {path.name}: 未找到 load_dotenv 锚点，跳过")
        return 0
    # 2) SQL：给 where status='closed' 加账户条件（f-string 内用 {ACCT}）
    n1 = src.count("where status='closed'")
    src = src.replace("where status='closed'", "where status='closed'{ACCT}", n1)
    n2 = src.count("where status = 'closed'")
    src = src.replace("where status = 'closed'", "where status = 'closed'{ACCT}", n2)
    # 3) 输出头部：打印口径
    src = src.replace('print("=" * 100)',
                      'print(describe_scope())\n    print("=" * 100)', 1)
    path.write_text(src, encoding="utf-8")
    print(f"  {path.name}: 注入 {n1 + n2} 处账户条件")
    return n1 + n2


def main() -> int:
    bakdir = ROOT / "_audit_ml" / f".scope_bak_{time.strftime('%Y%m%d_%H%M%S')}"
    bakdir.mkdir(parents=True, exist_ok=True)
    total = 0
    for name in TARGETS:
        p = ROOT / "_audit_ml" / name
        if not p.exists():
            print(f"  {name}: 不存在，跳过")
            continue
        shutil.copy2(p, bakdir / name)
        total += patch(p)
    print(f"备份目录: {bakdir}")
    print(f"合计注入: {total} 处")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
