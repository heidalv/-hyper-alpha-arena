# -*- coding: utf-8 -*-
"""[h884] 一次性清掉账本里的灰尘残仓(UNI/BNB 那种 <$5 的取整残渣),
写一笔相反的冲销腿,让"按币净仓"视图归零;引擎侧已有防再生的清扫。"""
import importlib.util
import io
import sys
from pathlib import Path

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
import psycopg  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from backend.services import lane_ledger  # noqa: E402

with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    cur.execute(
        """SELECT symbol, SUM((meta_json->>'qty')::float
             * CASE WHEN meta_json->>'side'='sell' THEN -1 ELSE 1 END) net,
             MAX(meta_json->>'fill_px')::float px, MAX(ts)
           FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'
           GROUP BY symbol
           HAVING ABS(SUM((meta_json->>'qty')::float
             * CASE WHEN meta_json->>'side'='sell' THEN -1 ELSE 1 END)) > 1e-9""")
    rows = cur.fetchall()
    n = 0
    for sym, net, px, last_ts in rows:
        notional = abs(float(net or 0) * float(px or 0))
        if notional >= 5.0:
            continue
        side = "buy" if float(net or 0) < 0 else "sell"
        from backend.services import lane_ledger  # noqa: E402
        lane_ledger.record_fill(
            lane_id="mm_asterdex", symbol=sym, side=side,
            qty=abs(float(net or 0)), fill_px=float(px or 0), mid_px=float(px or 0),
            fee_rate=0.0, price_bp=0.0,
            ts=(last_ts if last_ts else datetime.now(timezone.utc)),
            meta={"source": "F60_shadow", "flatten": True,
                  "notional": round(notional, 4), "price_usd": 0.0,
                  "exit_path": "dust_writeoff(h884)",
                  "exit_reason": "dust_writeoff",
                  "qty": abs(float(net or 0)), "side": side})
        print(f"  冲销 {sym:<10} 净 {float(net or 0):+.4f} @ {float(px or 0):.4f} "
              f"(名义 ${notional:.2f})")
        n += 1
    print(f"共冲销 {n} 个灰尘残仓")
    # [h884b] SKY 类:盘口已死(无行情)的真实残仓 —— 用最后一腿价格冲销
    cur.execute(
        """SELECT symbol, SUM((meta_json->>'qty')::float
             * CASE WHEN meta_json->>'side'='sell' THEN -1 ELSE 1 END) net,
             MAX(meta_json->>'fill_px')::float px, MAX(ts)
           FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'
           AND symbol NOT IN ('UNI','BNB')
           GROUP BY symbol
           HAVING ABS(SUM((meta_json->>'qty')::float
             * CASE WHEN meta_json->>'side'='sell' THEN -1 ELSE 1 END)) > 1e-9""")
    for sym, net, px, last_ts in cur.fetchall():
        if str(sym) != "SKY":
            continue
        side = "buy" if float(net or 0) < 0 else "sell"
        lane_ledger.record_fill(
            lane_id="mm_asterdex", symbol=sym, side=side,
            qty=abs(float(net or 0)), fill_px=float(px or 0), mid_px=float(px or 0),
            fee_rate=0.0, price_bp=0.0,
            ts=(last_ts if last_ts else datetime.now(timezone.utc)),
            meta={"source": "F60_shadow", "flatten": True,
                  "notional": round(abs(float(net or 0) * float(px or 0)), 4),
                  "price_usd": 0.0,
                  "exit_path": "dead_book_writeoff(h884b)",
                  "exit_reason": "dead_book_writeoff",
                  "qty": abs(float(net or 0)), "side": side})
        print(f"  死盘冲销 {sym}:净 {float(net or 0):+.4f} @ {float(px or 0):.4f}")
