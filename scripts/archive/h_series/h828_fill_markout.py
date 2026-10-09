# -*- coding: utf-8 -*-
"""[h828 用户"全面检查"后续] 用**真实成交**裁决三套标签口径。

分歧:同一批币,三套标签给出相反结论 ——
  ① 打到即成交(乐观,h817 研究件):PLAY +3.24bp ⇒ 开门
  ② 成交后条件期望(h823):−5.5 ~ −14.6bp ⇒ 关门
  ③ 排队被吃掉后成交 + 回补(最严,现生产门):−14.6 / −13.0bp ⇒ 关门

裁决办法:**不看模型,看实盘**。把车道账本里真实成交的挂单腿
(flow_entry_maker / flow_exit_maker,fee=0)拿出来,量它们成交之后
中价朝持仓方向的漂移(30/180/300 秒)。若为负 ⇒ 我们的挂单成交是"逆向选择",
口径②③ 对;若为正 ⇒ 口径① 对。

输出 data/flow_fill_markout.json(每笔 + 聚合 + t 值)。
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
import psycopg  # noqa: E402
from backend.services.market_maker.attribution import _market_dsn  # noqa: E402

HORIZONS = (30, 180, 300)
HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0

with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    cur.execute(
        "SELECT ts, symbol, meta_json->>'side',"
        " COALESCE((meta_json->>'fill_px')::float, 0),"
        " COALESCE((meta_json->>'mid_px')::float, 0),"
        " COALESCE(meta_json->>'exit_path','')"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND ts > now() - (%s * interval '1 hour')"
        " AND COALESCE(meta_json->>'exit_path','') IN"
        " ('flow_entry_maker','flow_exit_maker','flow_entry_taker')"
        " ORDER BY ts", (HOURS,))
    legs = cur.fetchall()

print(f"真实挂单成交腿:{len(legs)} 条(近 {HOURS}h)")
rows = []
with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
    for ts, sym, side, px, mid, path in legs:
        if not px or not mid:
            continue
        s = str(side or "").lower()
        if s in ("long", "b"):
            s = "buy"
        elif s in ("short", "s"):
            s = "sell"
        if s not in ("buy", "sell"):
            continue
        t_ms = int(ts.timestamp() * 1000)
        rec = {"symbol": str(sym), "side": s, "path": str(path),
               "fill_bp": round((mid - float(px)) / mid * 1e4, 3)}
        ok = True
        for hz in HORIZONS:
            cur.execute(
                "SELECT (bid_px+ask_px)/2.0 FROM asterdex_book_ticker"
                " WHERE symbol=%s AND event_ts_ms >= %s AND bid_px>0 AND ask_px>bid_px"
                " ORDER BY event_ts_ms LIMIT 1", (str(sym) + "USDT", t_ms + hz * 1000))
            r = cur.fetchone()
            if not r or not r[0]:
                ok = False
                break
            m2 = float(r[0])
            drift = (m2 - float(mid)) / float(mid) * 1e4
            rec[f"mo_{hz}s"] = round(drift if s == "buy" else -drift, 2)
        if ok:
            rows.append(rec)

if not rows:
    print("无可裁决样本(新引擎成交太少)")
else:
    print(f"有效样本:{len(rows)} 条")
    for r in rows[-14:]:
        print(f"  {r['symbol']:<8} {r['side']:<4} {r['path']:<18} "
              f"捕获 {r['fill_bp']:+6.2f}bp | 30s {r.get('mo_30s'):+7.2f} "
              f"180s {r.get('mo_180s'):+7.2f} 300s {r.get('mo_300s'):+7.2f}")
    print("聚合(均值 ± 标准误,t 值):")
    agg = {}
    for hz in HORIZONS:
        vals = [r[f"mo_{hz}s"] for r in rows if r.get(f"mo_{hz}s") is not None]
        if len(vals) >= 3:
            m = float(np.mean(vals))
            se = float(np.std(vals, ddof=1) / np.sqrt(len(vals)))
            t = m / se if se > 0 else 0.0
            agg[f"mo_{hz}s"] = {"n": len(vals), "mean_bp": round(m, 2),
                                "se_bp": round(se, 2), "t": round(t, 2)}
            print(f"  {hz:>4}s n={len(vals):>3} 均值 {m:+7.2f}bp ± {se:.2f} t={t:+.2f}")
    print(f"⇒ 裁决:{'挂单成交后漂移为正 ⇒ 口径① (打到即成交) 更接近实盘' if (agg.get('mo_180s', {}).get('mean_bp', -1) or -1) > 0 else '挂单成交后漂移为负 ⇒ 口径②③ (排队/逆向选择) 更接近实盘'}")
    (ROOT / "data" / "flow_fill_markout.json").write_text(
        json.dumps({"ts": time.time(), "hours": HOURS, "n": len(rows),
                    "agg": agg, "rows": rows[-40:]}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("  ✓ 已写 data/flow_fill_markout.json")
