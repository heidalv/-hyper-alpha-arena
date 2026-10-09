# -*- coding: utf-8 -*-
"""[h895] 重建 h828 经验层:真实挂单成交后的中价漂移(markout)30/180/300s。

h828 原脚本在并行清理中被删 ⇒ flow_fill_markout.json 停了 59 小时。
这是"概率分析"链的实证一环:它裁决各套标签口径更接近实盘。
口径:以每笔 lane 成交腿为锚,取成交后 30/180/300s 的中价漂移,按方向取符号
(正 = 价格朝持仓方向走 = 我们的成交没有逆向选择)。
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
from backend.services.market_maker.attribution import _market_dsn  # noqa: E402

HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0


def markouts():
    out = {}
    with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT symbol, meta_json->>'side', (meta_json->>'fill_px')::float, ts"
            " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
            " AND COALESCE(meta_json->>'exit_path','') LIKE 'flow_entry%'"
            f" AND ts > now() - interval '{HOURS} hours'")
        fills = cur.fetchall()
    if not fills:
        return {}
    with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
        for sym, side, px, ts in fills:
            sym2 = f"{sym}USDT" if "USDT" not in str(sym).upper() else sym
            key = str(sym).upper().replace("USDT", "")
            cur.execute(
                "SELECT event_ts_ms/1000.0, (bid_px+ask_px)/2.0"
                " FROM asterdex_book_ticker WHERE symbol=%s AND bid_px>0"
                " AND event_ts_ms BETWEEN %s AND %s ORDER BY event_ts_ms",
                (sym2, int(ts.timestamp() * 1000) - 5_000,
                 int(ts.timestamp() * 1000) + 320_000))
            rows = cur.fetchall()
            if len(rows) < 30:
                continue
            t_arr = np.array([r[0] for r in rows])
            mid = np.array([r[1] for r in rows])
            m0 = float(mid[0])
            if m0 <= 0:
                continue
            s = 1.0 if str(side).lower() == "buy" else -1.0
            for tag, hor in (("mo_30s", 30), ("mo_180s", 180), ("mo_300s", 300)):
                j = int(np.searchsorted(t_arr, t_arr[0] + hor, side="right") - 1)
                if j <= 0:
                    continue
                out.setdefault(tag, []).append(s * (float(mid[j]) / m0 - 1.0) * 1e4)
    agg = {}
    for tag, ys in out.items():
        ys = np.array(ys)
        m = float(ys.mean())
        se = float(ys.std(ddof=1) / np.sqrt(len(ys))) if len(ys) > 1 else 0.0
        agg[tag] = {"n": int(len(ys)), "mean_bp": round(m, 3),
                    "se_bp": round(se, 3), "t": round(m / se, 3) if se > 0 else 0.0}
    return agg


agg = markouts()
doc = {"ts": time.time(), "hours": HOURS, "agg": agg,
       "verdict": ("negative_drift" if any(v.get("mean_bp", 0) < 0 for v in agg.values())
                   else "positive_drift")}
(ROOT / "data" / "flow_fill_markout.json").write_text(
    json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
print("✓ 已写 flow_fill_markout.json")
for k, v in agg.items():
    print(f"  {k}: n={v['n']} 均值 {v['mean_bp']:+.2f}bp (t={v['t']:+.2f})")
