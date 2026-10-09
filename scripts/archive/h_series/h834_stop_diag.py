# -*- coding: utf-8 -*-
"""[h834 用户"查,真没有交易了"] 全链路停产诊断。

从"市场数据是否在流 → worker 是否在跑 → 四层门谁在挡 → 有没有成交"
逐层排查,并给出**确切的阻断点**。
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")

# ── 1. 市场数据是否在流 ──────────────────────────────────────────
from backend.services.market_maker.attribution import _market_dsn  # noqa: E402
import psycopg  # noqa: E402

with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
    cur.execute("SELECT max(event_ts_ms) FROM asterdex_trades")
    mx = cur.fetchone()[0]
    print(f"1) 市场数据:最新成交 {(time.time() - mx/1000.0):.0f} 秒前"
          if mx else "1) 市场数据:无")
    cur.execute("SELECT count(*) FROM asterdex_trades"
                " WHERE event_ts_ms > (extract(epoch from now())-300)*1000")
    print(f"   近 5 分钟成交行数: {cur.fetchone()[0]}")

# ── 2. worker 是否在跑 ──────────────────────────────────────────
st = ROOT / "logs" / "mm_lane_status.json"
if st.exists():
    d = json.loads(st.read_text(encoding="utf-8"))
    age = time.time() - st.stat().st_mtime
    print(f"2) worker 状态文件:{age:.0f} 秒前更新 | ticks={d.get('ticks')} "
          f"fills/h={d.get('fills_per_hour')}")
    print(f"   skip 前 6: {dict(sorted((d.get('skip_counts') or {}).items(), key=lambda x: -x[1])[:6])}")
    print(f"   挂单: {d.get('side_counts')}")
    print(f"   宇宙币数: {len(d.get('states') or {})}")

# ── 3. 四层门当前状态 ────────────────────────────────────────────
for name, key in (("situation", "flow_situation_last.json"),
                  ("gate", "flow_gate_last.json"),
                  ("layered", "flow_gate_layered.json")):
    p = ROOT / "data" / key
    if not p.exists():
        print(f"3) {name:<10} 文件缺失!")
        continue
    doc = json.loads(p.read_text(encoding="utf-8"))
    age_h = (time.time() - p.stat().st_mtime) / 3600.0
    if name == "situation":
        coins = doc.get("coins") or {}
        n_ok = sum(1 for c in coins.values()
                   for side in ("buy", "sell")
                   for row in (c.get(side) or [])
                   if float(row.get("mean_y") or -99) > 1.0 and float(row.get("n") or 0) >= 30)
        print(f"3) {name:<10} {age_h:.2f}h 前 | 币 {len(coins)} | 正期望桶 {n_ok}")
    else:
        gates = doc.get("gates") or {}
        n_open = sum(1 for v in gates.values() if v.get("allow"))
        print(f"3) {name:<10} {age_h:.2f}h 前 | 协议 {doc.get('protocol')} | "
              f"币 {len(gates)} | 开门 {n_open}")

# ── 4. 最近成交 ────────────────────────────────────────────────
_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    for mins in (15, 30, 60, 180):
        cur.execute(
            "SELECT count(*), SUM(net_bp*notional)/10000.0 FROM lane_ledger"
            " WHERE lane_id='mm_asterdex' AND event='fill'"
            " AND ts > now() - (%s * interval '1 minute')", (mins,))
        n, u = cur.fetchone()
        print(f"4) 近 {mins:>3} 分钟成交: {n} 腿 净 {float(u or 0):+.3f}U")
    cur.execute(
        "SELECT ts, symbol, COALESCE(meta_json->>'exit_path','?') FROM lane_ledger"
        " WHERE lane_id='mm_asterdex' AND event='fill'"
        " ORDER BY ts DESC LIMIT 3")
    print("   最后 3 笔:", [(r[0].strftime('%H:%M:%S'), r[1], r[2]) for r in cur.fetchall()])

# ── 5. 当前这一拍的真实情况(为什么被挡)──────────────────────────
d2 = json.loads(st.read_text(encoding="utf-8")) if st.exists() else {}
print("5) 各币当前(前 6 个):")
for sym, s in list((d2.get("states") or {}).items())[:6]:
    print(f"   {sym:<9} qty={s.get('qty')} quote=({s.get('quote_bid')},{s.get('quote_ask')})")
