# -*- coding: utf-8 -*-
"""[h776] 诊断 SI 的 −424bp 腿:出场路径/持仓龄/价差跳空证据。"""
import importlib.util
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
_spec = importlib.util.spec_from_file_location(
    "h425_trial", r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
import psycopg

with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    cur.execute(
        "SELECT ts, meta_json, spread_bp, price_bp, fee_bp, net_bp, notional"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill' AND symbol='SI'"
        " AND ts > now() - interval '40 minutes' ORDER BY ts")
    print("SI 近 40 分钟全部成交:")
    for r in cur.fetchall():
        m = r[1] or {}
        exit_p = m.get("exit_path") or "maker"
        print(f"  {r[0]:%H:%M:%S} side={m.get('side')} qty={m.get('qty')} "
              f"出场={exit_p} 持仓龄={m.get('age_sec', '?')}s | "
              f"捕获 {float(r[2] or 0):+7.1f}bp 价 {float(r[3] or 0):+8.1f}bp "
              f"费 {float(r[4] or 0):+6.1f}bp 净 {float(r[5] or 0):+8.1f}bp "
              f"名义 ${float(r[6] or 0):>6.0f}")
    cur.execute(
        "SELECT event_ts_ms, price FROM asterdex_trades WHERE symbol='SIUSDT'"
        " AND event_ts_ms > (extract(epoch from now())-3000)*1000 ORDER BY event_ts_ms")
    rows = cur.fetchall()
    if rows:
        lo, hi = min(x[1] for x in rows), max(x[1] for x in rows)
        print(f"SI 近 50 分钟真实成交 {len(rows)} 笔, 区间 [{lo:.4f}, {hi:.4f}]")
        # 5 分钟一档的极值走势(看跳空)
        print("  每 5 分钟极值(跳空证据):")
        from collections import defaultdict
        bkt = defaultdict(list)
        for ms, p in rows:
            bkt[int(ms // 300000)].append(p)
        for b in sorted(bkt):
            px = bkt[b]
            print(f"    {b * 300000 // 60000 % 60:02d}分档: 低 {min(px):.4f} 高 {max(px):.4f} n={len(px)}")
