# -*- coding: utf-8 -*-
"""Z107: 中长线链路的 paper/live 分叉普查（§56 复现脚本）。

方法：用 AST 找出「mode 条件分支」（`if X == "live"` / `!= "paper"` / `is_paper` …），
再看被该分支**保护**的语句体属于哪一类：
  - GUARD：体内有 `return False/None`、`block`、`reject`、`skip`、`raise` 等
    → **这是"某态才有保护"的信号**，需要人核验方向（谁更严）；
  - SIZE/PARAM：体内是仓位/参数计算；
  - ROUTING：体内是账户/通道/来源选择；
  - LOG：体内只有日志。
输出 JSON + 表格，供报告引用。
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]  # scripts → backend → 仓库根
MODULES = [
    "backend/services/full_auto/midlong_helpers.py",
    "backend/services/full_auto/midlong_executor.py",
    "backend/services/full_auto/midlong_position_manager.py",
    "backend/services/full_auto/proposal_execution.py",
    "backend/services/full_auto/paper_execution.py",
    "backend/services/full_auto/master_execution.py",
    "backend/services/full_auto/mlto_cycle.py",
    "backend/services/factor_engine/midlong_factor_route.py",
    "backend/services/mlto/brain.py",
]
MODE_RX = re.compile(r"""(\w*mode\b|\bis_paper\b|\bis_live\b)""", re.I)
# 由 mode **比较**派生的布尔标志（如 `is_paper = (... ) == "paper"`）
FLAG_RX = re.compile(r"""[!=]=\s*["'](paper|live)["']""", re.I)
GUARD_RX = re.compile(r"\b(return\s+(False|None)|block|blocked|reject|skip|raise|deny)\b", re.I)
SIZE_RX = re.compile(r"(size|margin|notional|leverage|pct|mult|quantit)", re.I)
ROUTING_RX = re.compile(r"(account_id|channel|exchange|executor|get_trading_account_id|source)", re.I)


def _collect_mode_flags(tree: ast.AST) -> set:
    """收集「由 paper/live 表达式派生的局部标志名」：is_paper / _is_live_mid …"""
    flags = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            try:
                rhs = ast.unparse(node.value)
            except Exception:
                continue
            if not FLAG_RX.search(rhs):
                continue
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    flags.add(tgt.id)
    return flags


def _classify(body: List[ast.stmt]) -> str:
    try:
        code = "\n".join(ast.unparse(s) for s in body)
    except Exception:
        return "UNKNOWN"
    if GUARD_RX.search(code):
        return "GUARD"
    if SIZE_RX.search(code):
        return "SIZE/PARAM"
    if ROUTING_RX.search(code):
        return "ROUTING"
    return "LOG/OTHER"


def main() -> int:
    rows: List[Dict[str, Any]] = []
    for rel in MODULES:
        p = ROOT / rel
        if not p.exists():
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        flags = _collect_mode_flags(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            try:
                test = ast.unparse(node.test)
            except Exception:
                continue
            is_mode = bool(MODE_RX.search(test)) or any(
                re.search(rf"(?<![\w.]){re.escape(f)}(?![\w])", test) for f in flags
            )
            if not is_mode:
                continue
            rows.append({
                "file": rel,
                "line": node.lineno,
                "test": re.sub(r"\s+", " ", test)[:120],
                "kind": _classify(node.body),
                "line_n": len(node.body),
                "via_flag": (not MODE_RX.search(test)),
            })
    # 汇总
    from collections import Counter
    print(f"mode 条件分支总数: {len(rows)}")
    print("按类别:", dict(Counter(r["kind"] for r in rows)))
    print("\n=== GUARD 类（某态才有保护，需人核验方向）===")
    for r in rows:
        if r["kind"] == "GUARD":
            print(f"  {r['file']}:{r['line']}  if {r['test']}")
    print("\n=== ROUTING 类 ===")
    for r in rows:
        if r["kind"] == "ROUTING":
            print(f"  {r['file']}:{r['line']}  if {r['test']}")
    out = ROOT / "data" / "paper_live_divergence.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
