# -*- coding: utf-8 -*-
"""[h860 桥 01:58] 只统计"深入修复之后"的窗口(排除修复前的旧账)。"""
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

# 深入修复的最后一层(止损 60 + 被动离场)落在 01:35 左右
CUT = time.time() - 1800          # 近 30 分钟(覆盖修复后窗口)
rows = [json.loads(x) for x in (ROOT / "data" / "flow_roundtrip_log.jsonl")
        .read_text(encoding="utf-8").splitlines() if x.strip()]
rec = [r for r in rows if float(r.get("ts") or 0) > CUT and r.get("y_bp") is not None]
mk = [r for r in rec if float(r.get("fee_bp") or 0) == 0]
tk = [r for r in rec if float(r.get("fee_bp") or 0) > 0]
print(f"== 修复后窗口(近 30 分钟)往返 {len(rec)} 条 ==")
if mk:
    ys = [float(r["y_bp"]) for r in mk]
    print(f"  挂单离场 n={len(mk):>3} 平均 **{np.mean(ys):+7.2f}bp** "
          f"(合计 {sum(ys):+.0f}bp)")
if tk:
    ys = [float(r["y_bp"]) for r in tk]
    print(f"  吃单止损 n={len(tk):>3} 平均 {np.mean(ys):+7.2f}bp (合计 {sum(ys):+.0f}bp)")
if rec:
    print(f"  挂单占比 **{len(mk)/len(rec)*100:.0f}%**(止损率 {len(tk)/len(rec)*100:.0f}%)"
          f" ⇒ 组合期望 {np.mean([float(r['y_bp']) for r in rec]):+.2f}bp/笔")

with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    for mins in (15, 30):
        cur.execute(
            "SELECT count(*), SUM(net_bp*notional)/10000.0 FROM lane_ledger"
            " WHERE lane_id='mm_asterdex' AND event='fill'"
            " AND ts > now() - (%s * interval '1 minute')", (mins,))
        n, u = cur.fetchone()
        print(f"  近 {mins} 分钟: {n:>3} 腿 净 **{float(u or 0):+8.3f}U**")
    cur.execute(
        "SELECT date_trunc('minute', ts) m, SUM(net_bp*notional)/10000.0 u"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND ts > now() - interval '40 minutes' GROUP BY 1 ORDER BY 1")
    print("  逐分钟净额(近 40 分钟):")
    line = []
    for m, u in cur.fetchall():
        line.append(f"{m:%H:%M} {float(u or 0):+.2f}")
    for i in range(0, len(line), 5):
        print("    " + " | ".join(line[i:i + 5]))
