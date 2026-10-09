# -*- coding: utf-8 -*-
"""收官核查：**分析宇宙 ⊆ 采集宇宙** 吗？（只读）

分析宇宙来源（取最近的运行态证据）：
  · 日志里 `[MidLongAgent独立] batch=[...]` / `fixed long=[...]` / `ai_long=[...]`
  · AI 看板（coin_select_candidates，mid/long）
  · 已开持仓（paper_positions open）
采集宇宙来源：`kline_history_sync._depth_symbols(max_symbols=60)`
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ── 采集宇宙 ──
from backend.services.kline_history_sync import _analysis_board_symbols, _depth_symbols  # noqa: E402

collect = set(_depth_symbols(max_symbols=60))
board = set(_analysis_board_symbols())

# ── 分析宇宙：日志 + DB ──
analysis: set = set()
t = (ROOT / "logs" / "backend.log").read_text(encoding="utf-8", errors="replace").splitlines()
recent = [ln for ln in t if ln[:16] >= "2026-09-18 14:00"]
for ln in recent:
    for pat in (r"\[MidLongAgent独立\] batch=\[([^\]]*)\]",
                r"固定long=\[([^\]]*)\]",
                r"ai_long=\[([^\]]*)\]",
                r"ai_mid=\[([^\]]*)\]"):
        m = re.search(pat, ln)
        if m:
            for s in re.findall(r"'([A-Z0-9]+)'", m.group(1)):
                analysis.add(s)

db_syms = {"board": set(), "positions": set(), "session": set()}
try:
    from sqlalchemy import text
    from backend.database.connection import SessionLocal
    db = SessionLocal()
    try:
        db.execute(text("SET app.is_admin='on'"))
        for (s,) in db.execute(text(
                "SELECT DISTINCT symbol FROM paper_positions WHERE status='open'")).fetchall():
            db_syms["positions"].add(str(s).upper())
        for (s,) in db.execute(text(
                "SELECT DISTINCT symbol FROM coin_select_candidates WHERE listed IS TRUE "
                "AND horizon IN ('mid','long','midlong')")).fetchall():
            db_syms["board"].add(str(s).upper())
        for (s,) in db.execute(text(
                "SELECT symbols FROM full_auto_sessions WHERE status IN "
                "('running','defensive','paused')")).fetchall():
            if s:
                db_syms["session"] |= {x.strip().upper() for x in str(s).replace("[", "").replace("]", "")
                                       .replace("'", "").replace('"', "").split(",") if x.strip()}
    finally:
        db.close()
except Exception as exc:  # noqa: BLE001
    print(f"（DB 读取失败: {type(exc).__name__}: {str(exc)[:100]}）")

print("=" * 92)
print("采集宇宙 vs 分析宇宙")
print("=" * 92)
print(f"  采集宇宙（_depth_symbols，{len(collect)} 个）: {sorted(collect)}")
for k, v in db_syms.items():
    if v:
        analysis |= v
        print(f"  {k:10s}（{len(v)}）: {sorted(v)}")
log_syms = {s for s in analysis}
print(f"\n  分析宇宙合计（{len(log_syms)} 个）: {sorted(log_syms)}")

missing = sorted(s for s in log_syms if s and s not in collect)
print(f"\n  ⚠️ **分析宇宙里但不在采集宇宙**（{len(missing)} 个）: {missing}")
if not missing:
    print("  ⇒ ✅ 全覆盖：分析宇宙 ⊆ 采集宇宙")
else:
    print("  ⇒ 仍有缺口：这些标的的长周期不会被回填（主脑会判 K线:* 硬缺项）")
print(f"\n  看板标的（{len(board)}）: {sorted(board)}；其中不在采集宇宙的: "
      f"{sorted(s for s in board if s not in collect) or '无'}")
