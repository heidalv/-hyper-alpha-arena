# -*- coding: utf-8 -*-
"""[h851 用户"手续费将利润吞没了"] 手续费归因量化。

三个层面:
  ① 账本口径:近 N 小时的手续费总额 vs 净额 vs 毛额(价格+价差);
  ② 往返口径:按离场路径看"有多少往返付了吃单费",以及吃单往返 vs 挂单往返的净额;
  ③ 反事实:若把吃单止损换成挂单离场(省掉 4bp/次),净额会变成多少。
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

with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    for hrs in (1, 6, 24):
        cur.execute(
            "SELECT count(*), SUM(COALESCE(fee_bp,0)*notional)/10000.0,"
            " SUM((COALESCE(spread_bp,0)+COALESCE(price_bp,0))*notional)/10000.0,"
            " SUM(net_bp*notional)/10000.0"
            " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
            " AND ts > now() - (%s * interval '1 hour')", (hrs,))
        n, fee, gross, net = cur.fetchone()
        fee = float(fee or 0)
        gross = float(gross or 0)
        net = float(net or 0)
        print(f"① 近 {hrs:>2}h:{n:>5} 腿 | 手续费 {fee:+.3f}U | 毛额(价差+价格) "
              f"{gross:+.3f}U | 净 {net:+.3f}U | **手续费/毛额 = "
              f"{(abs(fee)/abs(gross)*100 if gross else 0):.0f}%**")
    # ② 按出场路径
    cur.execute(
        "SELECT COALESCE(meta_json->>'exit_path','maker') p, count(*),"
        " SUM(COALESCE(fee_bp,0)*notional)/10000.0, SUM(net_bp*notional)/10000.0,"
        " AVG(net_bp)"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND ts > now() - interval '6 hours' GROUP BY 1 ORDER BY 4")
    print("② 近 6h 按出场路径:")
    for r in cur.fetchall():
        print(f"   {str(r[0])[:20]:<20} n={r[1]:>4} 手续费 {float(r[2] or 0):+7.3f}U "
              f"净 {float(r[3] or 0):+8.3f}U 每腿 {float(r[4] or 0):+6.2f}bp")

# ③ 往返口径
rows = [json.loads(x) for x in (ROOT / "data" / "flow_roundtrip_log.jsonl")
        .read_text(encoding="utf-8").splitlines() if x.strip()]
if rows:
    now = time.time()
    recent = [r for r in rows if float(r.get("ts") or 0) > now - 6 * 3600]
    taker = [r for r in recent if float(r.get("fee_bp") or 0) > 0]
    maker = [r for r in recent if float(r.get("fee_bp") or 0) == 0]
    print(f"③ 往返(近 6h)共 {len(recent)} 条:")
    for tag, grp in (("挂单往返(0 费)", maker), ("吃单往返(4bp)", taker)):
        if grp:
            ys = [float(r["y_bp"]) for r in grp if r.get("y_bp") is not None]
            print(f"   {tag:<16} n={len(grp):>3} 平均 y={np.mean(ys):+7.2f}bp "
                  f"合计 {sum(ys):+8.1f}bp 占比 {len(grp)/len(recent)*100:.0f}%")
    if taker:
        ys = [float(r["y_bp"]) for r in taker if r.get("y_bp") is not None]
        print(f"   ⇒ 反事实:吃单往返若省掉 4bp/次,合计从 {sum(ys):+.1f}bp "
              f"变成 {sum(ys) + 4.0 * len(ys):+.1f}bp"
              f"(每笔仍可能因滑点比挂单差)")
