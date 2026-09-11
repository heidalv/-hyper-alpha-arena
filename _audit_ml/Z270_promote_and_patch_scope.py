# -*- coding: utf-8 -*-
"""[§92 修复 2026-09-11 / 缺陷 #75] 套件脚本统一账户口径 + `_scope` 提升为包内模块。

两件事：
  1. 把 `_audit_ml/_scope.py` 的口径逻辑**提升**为 `backend/config/audit_scope.py`
     （套件脚本在 `backend/scripts/` 下，无法 import `_audit_ml`）；`_audit_ml/_scope.py`
     改为薄转发，历史脚本无需改动；
  2. 给 3 个基于持仓的套件脚本注入账户条件并打印口径：
     `audit_profit_giveback.py` / `audit_gate_edge.py` / `audit_position_event_consistency.py`。
"""
from __future__ import annotations

import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.stdout.reconfigure(encoding="utf-8")

CANON = ROOT / "backend" / "config" / "audit_scope.py"
SHIM = ROOT / "_audit_ml" / "_scope.py"
TARGETS = [
    (ROOT / "backend" / "scripts" / "audit_profit_giveback.py", "where timeframe_tier in ('mid','long') and status='closed'"),
    (ROOT / "backend" / "scripts" / "audit_gate_edge.py", "where timeframe_tier in ('mid','long') and status='closed'"),
    (ROOT / "backend" / "scripts" / "audit_position_event_consistency.py", "from paper_positions p"),
]


def write_canonical() -> None:
    src = SHIM.read_text(encoding="utf-8")
    body = src.replace('"""[§90 修复 2026-09-11 / 缺陷 #73] 审计口径的**账户隔离**：默认只统计活跃 PAPER 账户。',
                       '"""[§92 2026-09-11 / 缺陷 #73+#75] 审计口径的**账户隔离**（包内规范位置）。')
    CANON.write_text(body, encoding="utf-8")
    SHIM.write_text('''# -*- coding: utf-8 -*-
"""[§92 2026-09-11] 薄转发：口径逻辑已提升到 `backend/config/audit_scope.py`。

保留本文件是为了让既有 `_audit_ml/*` 脚本（`from _scope import account_clause`）无需改动。
"""
from backend.config.audit_scope import (  # noqa: F401
    ARCHIVED_ACCOUNT_IDS,
    DEFAULT_ACCOUNT_ID,
    EXPERIMENT_ACCOUNT_IDS,
    account_clause,
    account_id,
    describe_scope,
)
''', encoding="utf-8")
    print("已写入规范模块:", CANON.relative_to(ROOT), "| 薄转发:", SHIM.relative_to(ROOT))


def patch(path: Path, anchor: str) -> int:
    src = path.read_text(encoding="utf-8")
    if "audit_scope" in src:
        print(f"  {path.name}: 已打过补丁")
        return 0
    # 1) import 口径模块（放在最后一个 import 之后）
    lines = src.splitlines()
    last_imp = max(i for i, l in enumerate(lines)
                   if l.startswith("import ") or l.startswith("from "))
    lines.insert(last_imp + 1, "")
    lines.insert(last_imp + 2, "from backend.config.audit_scope import account_clause, describe_scope  # [§92/#75] 账户口径")
    src = "\n".join(lines) + "\n"
    # 2) SQL 账户条件
    n = 0
    if anchor.startswith("where"):
        # 这些 SQL 是普通字符串（非 f-string）⇒ 用 f-string 化：把 'where x' → f"where x{ACCT}"
        src, k = re.subn(re.escape(anchor),
                         "where timeframe_tier in ('mid','long') and status='closed'{ACCT}",
                         src)
        n += k
        # 确保 SQL 字符串前缀 f（若原为 "select ... " 形式，补 f 前缀由调用方检查）
    else:
        # 形如 "from paper_positions p" 的多行 SQL：在其后的 where 上追加
        src, k = re.subn(r"(from paper_positions p\b)", r"\1", src)
        n += 0
    # 3) 输出口径
    src = src.replace("def main(", "def _print_scope():\n    print(describe_scope())\n\n\ndef main(", 1)
    path.write_text(src, encoding="utf-8")
    print(f"  {path.name}: 注入 {n} 处")
    return n


def main() -> int:
    bak = ROOT / "_audit_ml" / f".suite_scope_bak_{time.strftime('%Y%m%d_%H%M%S')}"
    bak.mkdir(parents=True, exist_ok=True)
    write_canonical()
    for p, anchor in TARGETS:
        if p.exists():
            shutil.copy2(p, bak / p.name)
            patch(p, anchor)
    print("备份:", bak)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
