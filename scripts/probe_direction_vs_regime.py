# -*- coding: utf-8 -*-
"""根因追问：① 主脑方向分布（是否清一色 short）② 空头闸用的日线 regime 与主脑输入是否同口径。只读。"""
from __future__ import annotations

import io
import json
import sys
from collections import Counter
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402
from backend.database.connection import AnalyticsSessionLocal  # noqa: E402

print("=" * 96)
print("① 主脑方向分布（ai_decision_logs 的 _brain_decision_log 快照，近 3 小时）")
db = AnalyticsSessionLocal()
try:
    db.execute(text("SET app.is_admin='on'"))
    rows = db.execute(text("""
        SELECT symbol, decision_snapshot, created_at FROM ai_decision_logs
        WHERE created_at >= now() - interval '3 hours'
          AND decision_snapshot LIKE '%_brain_decision_log%'
        ORDER BY id DESC LIMIT 2000
    """)).fetchall()
    print(f"   取到 {len(rows)} 条")
    per_sym = Counter()
    dirs = Counter()
    srcs = Counter()
    conv = {}
    for sym, snap, ts in rows:
        try:
            d = json.loads(snap)
        except Exception:  # noqa: BLE001
            continue
        if not d.get("_brain_decision_log"):
            continue
        dr = str(d.get("direction") or "")
        dirs[dr] += 1
        srcs[str(d.get("dir_src") or "")] += 1
        per_sym[(str(sym), dr)] += 1
        if isinstance(d.get("llm_conviction"), (int, float)):
            conv.setdefault(dr, []).append(float(d["llm_conviction"]))
    print(f"   direction 分布: {dict(dirs)}")
    print(f"   dir_src   分布: {dict(srcs)}")
    for dr, v in conv.items():
        print(f"   {dr}: conviction 均值={sum(v)/len(v):.1f} n={len(v)}")
    print("\n   (symbol, direction) Top15:")
    for (s, dr), n in per_sym.most_common(15):
        print(f"      {s:10s} {dr:8s} n={n}")
finally:
    db.close()

print("\n" + "=" * 96)
print("② 空头闸用的日线 regime（运行态实测） vs 主脑 extras 里的趋势字段")
try:
    from backend.services.full_auto.midlong_circuit_gate import _daily_regime, _long_mode
    from backend.services.mlto import brain as B
    SYMS = ["VIRTUAL", "ASTER", "XRP", "UNI", "ZEC", "1000PEPE", "SOL", "BNB", "BTC", "DOGE", "LINK"]
    print(f"   _long_mode() = {_long_mode()}")
    print(f"   {'symbol':10s} {'日线regime':>10s}   （空头闸：仅 down 放行）")
    for s in SYMS:
        try:
            print(f"   {s:10s} {str(_daily_regime(s)):>10s}")
        except Exception as exc:  # noqa: BLE001
            print(f"   {s:10s} <err {type(exc).__name__}>")
    print("\n   主脑 extras 里与'趋势/方向'有关的键（build_feed）：")
    try:
        feed = B.build_feed(symbol="VIRTUAL", tier="mid", session_id="probe", market_summary=None)
        ex = feed.get("extras") or {}
        for k in ("long_trend_v2", "trend_e1", "factor_route", "current_position", "role_reminder"):
            if k in ex:
                print(f"      {k:18s} = {json.dumps(ex[k], ensure_ascii=False)[:160]}")
    except Exception as exc:  # noqa: BLE001
        print(f"      （build_feed 失败: {type(exc).__name__}: {str(exc)[:90]}）")
except Exception as exc:  # noqa: BLE001
    print(f"   导入失败: {type(exc).__name__}: {str(exc)[:120]}")
