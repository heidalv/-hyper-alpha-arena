# -*- coding: utf-8 -*-
"""[h812] 稳健性验证:h810 里正期望组合是不是运气。

对候选{币 × 时限}:换 3 种分割(50/50、60/40、70/30)× 2 个随机种子,
看样本外边缘的中位数是否仍为正(防过拟合/防单次运气)。
"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
from backend.services.market_maker.attribution import _market_dsn  # noqa: E402
import psycopg  # noqa: E402
from sklearn.ensemble import HistGradientBoostingRegressor  # noqa: E402

CAND = [("BTW", 900), ("BTW", 300), ("PUMP", 900), ("SI", 300), ("SI", 90),
        ("MOVR", 900), ("MAGMA", 900), ("LYN", 900), ("US", 300), ("PLAY", 900)]
HOURS = 24
STEP_MS = 15000
COST_BP = 6.0
FEATS = ["ofi_15s", "ofi_60s", "ofi_300s", "ret_15s_bp", "ret_60s_bp",
         "ret_300s_bp", "spread_bp", "depth_imb", "vwap_dev_bp", "vol_300s_bp"]


def main() -> int:
    now_ms = int(time.time() * 1000)
    lo_ms = now_ms - HOURS * 3600 * 1000
    out = []
    with psycopg.connect(_market_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cache = {}
        for sym, hz_s in CAND:
            if sym not in cache:
                cur.execute(
                    "SELECT event_ts_ms, bid_px, bid_qty, ask_px, ask_qty FROM"
                    " asterdex_book_ticker WHERE symbol=%s AND event_ts_ms > %s"
                    " AND bid_px>0 AND ask_px>bid_px ORDER BY event_ts_ms",
                    (sym + "USDT", lo_ms))
                bk = pd.DataFrame(cur.fetchall(), columns=["ts", "bp", "bq", "ap", "aq"])
                cur.execute(
                    "SELECT event_ts_ms, price, qty, is_buyer_maker FROM asterdex_trades"
                    " WHERE symbol=%s AND event_ts_ms > %s ORDER BY event_ts_ms",
                    (sym + "USDT", lo_ms))
                tr = pd.DataFrame(cur.fetchall(), columns=["ts", "px", "qty", "bm"])
                if len(bk) < 1000 or len(tr) < 100:
                    cache[sym] = None
                    continue
                cache[sym] = (bk, tr)
            if cache[sym] is None:
                continue
            bk, tr = cache[sym]
            bk = bk.astype({"bp": float, "bq": float, "ap": float, "aq": float})
            bk["mid"] = (bk["bp"] + bk["ap"]) / 2.0
            tr = tr.astype({"px": float, "qty": float})
            tr["signed"] = np.where(tr["bm"], -tr["qty"] * tr["px"], tr["qty"] * tr["px"])
            grid = np.arange(lo_ms + 300000, now_ms - 900000, STEP_MS)
            t = pd.DataFrame({"t": grid})
            m = pd.merge_asof(t, bk[["ts", "mid", "bq", "aq", "bp", "ap"]].rename(
                columns={"ts": "t"}), on="t", direction="backward")
            df = pd.DataFrame({"t": grid, "mid": m["mid"].values,
                               "bq": m["bq"].values, "aq": m["aq"].values})
            df["spread_bp"] = ((m["ap"].values - m["bp"].values)
                               / df["mid"].replace(0, np.nan) * 1e4)
            df["depth_imb"] = (df["bq"] - df["aq"]) / (df["bq"] + df["aq"] + 1e-9)
            for w_s in (15, 60, 300):
                mw = pd.merge_asof(t - w_s * 1000, bk[["ts", "mid"]].rename(
                    columns={"ts": "t", "mid": "mp"}), on="t", direction="backward")
                df[f"ret_{w_s}s_bp"] = (df["mid"] / mw["mp"].values - 1.0) * 1e4
                vals = []
                for x in grid:
                    seg = tr.loc[(tr["ts"] >= x - w_s * 1000) & (tr["ts"] < x)]
                    g = float((seg["px"] * seg["qty"]).sum())
                    vals.append(float(seg["signed"].sum()) / g if g > 0 else 0.0)
                df[f"ofi_{w_s}s"] = vals
            df["vwap_dev_bp"] = (df["mid"] / df["mid"].rolling(4, min_periods=1).mean()
                                 - 1.0) * 1e4
            df["vol_300s_bp"] = (df["mid"].pct_change(fill_method=None) * 1e4).rolling(
                20, min_periods=5).std()
            edges = []
            for frac in (0.5, 0.6, 0.7):
                for seed in (7, 21):
                    d2 = df.copy()
                    d2["target_bp"] = (d2["mid"].shift(-int(hz_s * 1000 / STEP_MS))
                                       - d2["mid"]) / d2["mid"] * 1e4
                    d2 = d2.replace([np.inf, -np.inf], np.nan).dropna(
                        subset=FEATS + ["target_bp"])
                    if len(d2) < 400:
                        continue
                    split = int(len(d2) * frac)
                    Xtr, Xte = d2[FEATS].iloc[:split], d2[FEATS].iloc[split:]
                    ytr, yte = d2["target_bp"].iloc[:split], d2["target_bp"].iloc[split:]
                    reg = HistGradientBoostingRegressor(
                        max_iter=120, learning_rate=0.07, max_depth=4,
                        min_samples_leaf=40, random_state=seed).fit(Xtr, ytr)
                    sel = reg.predict(Xte) >= COST_BP
                    if sel.sum() >= 15:
                        edges.append(float((yte[sel] - COST_BP).mean()))
            if edges:
                med = float(np.median(edges))
                out.append({"symbol": sym, "hz_sec": hz_s, "n_runs": len(edges),
                            "median_edge_bp": round(med, 2),
                            "min_edge_bp": round(min(edges), 2),
                            "max_edge_bp": round(max(edges), 2),
                            "positive_runs": sum(1 for e in edges if e > 0)})
                print(f"  {sym:<8} {hz_s:>4}s 运行 {len(edges)} 次:中位 {med:+7.2f}bp "
                      f"[{min(edges):+.1f},{max(edges):+.1f}] 正 {sum(1 for e in edges if e>0)}/{len(edges)}")
            else:
                print(f"  {sym:<8} {hz_s:>4}s 入选样本不足")
    out.sort(key=lambda r: -r["median_edge_bp"])
    print("\n== 稳健组合(中位 > +5bp 且 正次数过半)==")
    for r in out:
        if r["median_edge_bp"] > 5 and r["positive_runs"] * 2 > r["n_runs"]:
            print(f"  {r['symbol']:<8} {r['hz_sec']:>4}s 中位 {r['median_edge_bp']:+.2f}bp "
                  f"({r['positive_runs']}/{r['n_runs']})")
    (ROOT / "data" / "flow_edge_robust.json").write_text(
        json.dumps({"ts": time.time(), "cost_bp": COST_BP, "results": out},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print("  ✓ 已写 data/flow_edge_robust.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
