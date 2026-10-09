# -*- coding: utf-8 -*-
"""[h862] 从 239 条往返里挖"哪种进场能赚":把往返与它的入场腿配对,看入场时刻的可观测条件。

可用的入场侧条件(账本 meta):①入场捕获(成交价 vs 当时中价)②名义(风险档)
③方向 ④币 ⑤时段 ⑥持仓时长。
目标:找出**正期望的子集**,把它变成进场门(而不是继续无条件探索)。
"""
import importlib.util
import io
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
import psycopg  # noqa: E402

# ① 入场腿(时间序)
with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    cur.execute(
        "SELECT ts, symbol, meta_json->>'side', (meta_json->>'fill_px')::float,"
        " (meta_json->>'mid_px')::float, COALESCE((meta_json->>'notional')::float,0),"
        " COALESCE((meta_json->>'exit_path'),'')"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND ts > now() - interval '8 hours' ORDER BY ts")
    legs = cur.fetchall()

entries = []
for ts, sym, side, px, mid, notional, path in legs:
    if str(path) != "flow_entry_maker":
        continue
    if not px or not mid:
        continue
    s = str(side or "").lower()
    cap = ((float(mid) - float(px)) / float(mid) * 1e4) if s == "buy" \
        else ((float(px) - float(mid)) / float(mid) * 1e4)
    entries.append({"ts": float(ts.timestamp()), "symbol": str(sym), "side": s,
                    "cap_bp": cap, "notional": float(notional or 0)})

# ② 往返日志
rows = [json.loads(x) for x in (ROOT / "data" / "flow_roundtrip_log.jsonl")
        .read_text(encoding="utf-8").splitlines() if x.strip()]
rows = [r for r in rows if r.get("y_bp") is not None]

# ③ 配对:同一币、入场在前 300 秒内最近的入场腿
pairs = []
for r in rows:
    t = float(r.get("ts") or 0)
    sym = str(r.get("symbol") or "")
    cand = [e for e in entries if e["symbol"] == sym and 0 <= t - e["ts"] <= 300]
    if not cand:
        continue
    e = max(cand, key=lambda x: x["ts"])
    pairs.append({**r, "cap_bp": e["cap_bp"], "notional": e["notional"],
                  "side": e["side"], "hold": t - e["ts"]})
print(f"配对成功 {len(pairs)}/{len(rows)} 条往返")

def bucket(name, fn, edges):
    print(f"\n== {name} ==")
    for lo, hi in zip(edges[:-1], edges[1:]):
        grp = [float(p["y_bp"]) for p in pairs if lo <= fn(p) < hi]
        if len(grp) >= 12:
            m = float(np.mean(grp))
            se = float(np.std(grp, ddof=1) / np.sqrt(len(grp)))
            t = m / se if se > 0 else 0.0
            print(f"  [{lo:>6.1f},{hi:>6.1f}) n={len(grp):>3} 平均 {m:+7.2f}bp "
                  f"t={t:+5.2f}{'  ★正' if m > 0 and t > 1.5 else ''}")

bucket("按入场捕获 cap_bp", lambda p: p["cap_bp"], [-99, 0, 2, 4, 8, 99])
bucket("按名义(风险档)", lambda p: p["notional"], [0, 150, 250, 400, 99999])
bucket("按持仓时长", lambda p: p["hold"], [0, 20, 40, 60, 999])
print("\n== 按方向 ==")
for s in ("buy", "sell"):
    grp = [float(p["y_bp"]) for p in pairs if p["side"] == s]
    if len(grp) >= 12:
        m = float(np.mean(grp))
        se = float(np.std(grp, ddof=1) / np.sqrt(len(grp)))
        print(f"  {s:<5} n={len(grp):>3} 平均 {m:+7.2f}bp t={m/se if se else 0:+.2f}")
print("\n== 按挂单/吃单离场 × 入场捕获 ==")
for tag, sel in (("挂单离场", lambda p: float(p.get("fee_bp") or 0) == 0),
                 ("吃单止损", lambda p: float(p.get("fee_bp") or 0) > 0)):
    grp = [p for p in pairs if sel(p)]
    caps = [p["cap_bp"] for p in grp]
    if grp:
        print(f"  {tag}: n={len(grp)} 平均入场捕获 {np.mean(caps):+.2f}bp "
              f"平均 y={np.mean([float(p['y_bp']) for p in grp]):+.1f}bp")
