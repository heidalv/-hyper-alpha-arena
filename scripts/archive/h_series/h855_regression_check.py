# -*- coding: utf-8 -*-
"""[h855] 判断−16U/15min 是市场还是改动:看大盘方向 + 入场方向分布 + 各腿来源。"""
import importlib.util
import io
import sys
import time
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
from backend.services.market_maker.attribution import _market_dsn  # noqa: E402

with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        cur.execute(
            "SELECT (bid_px+ask_px)/2.0 FROM asterdex_book_ticker WHERE symbol=%s"
            " AND event_ts_ms > (extract(epoch from now())-1200)*1000"
            " AND bid_px>0 ORDER BY event_ts_ms LIMIT 1", (sym,))
        a = cur.fetchone()
        cur.execute(
            "SELECT (bid_px+ask_px)/2.0 FROM asterdex_book_ticker WHERE symbol=%s"
            " AND bid_px>0 ORDER BY event_ts_ms DESC LIMIT 1", (sym,))
        b = cur.fetchone()
        if a and b and a[0]:
            print(f"  {sym:<9} 20 分钟涨跌 {(float(b[0])/float(a[0])-1)*1e4:+.1f}bp")

with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    cur.execute(
        "SELECT meta_json->>'side' s, count(*), AVG(net_bp),"
        " SUM(net_bp*notional)/10000.0"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND ts > now() - interval '20 minutes' GROUP BY 1")
    print("近 20 分钟按方向:", [(r[0], r[1], round(float(r[2] or 0), 1),
                                round(float(r[3] or 0), 2)) for r in cur.fetchall()])
    cur.execute(
        "SELECT symbol, meta_json->>'side', COALESCE(meta_json->>'exit_path','?'),"
        " net_bp, notional, ts FROM lane_ledger WHERE lane_id='mm_asterdex'"
        " AND event='fill' AND COALESCE(meta_json->>'exit_path','')='taker_stop'"
        " AND ts > now() - interval '20 minutes' ORDER BY net_bp LIMIT 10")
    print("近 20 分钟的吃单止损明细:")
    for r in cur.fetchall():
        print(f"  {r[5]:%H:%M:%S} {str(r[0]):<8} {str(r[1]):<5} {float(r[3] or 0):+7.1f}bp "
              f"名义 {float(r[4] or 0):.0f}")
