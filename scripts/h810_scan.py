# -*- coding: utf-8 -*-
"""[h810 2026-10-04 用户"继续"] 全市场扫描:哪些币 × 哪个时限真的有可交易 edge。

做法(与 h807/h808 同口径,可批量):
  1. 取数据里成交最活跃的 N 个币(按近 H 小时成交笔数);
  2. 每个币 × 每个时限{90s, 300s, 900s}:walk-forward 训练 GBM,
     样本外评估两条决策规则:
       · 回归规则:pred ≥ 成本(6bp)⇒ 选中样本的样本外平均(扣成本)
       · 校准规则:P(方向) ≥ 0.55 ⇒ 选中样本的样本外平均(扣成本)
  3. 输出排行榜:哪些币 × 时限 的样本外每笔期望 > 0(**只在有统计功效时**)。
结论直接喂给:宇宙选币(只留有 edge 的币)+ 模型门(开门条件)。
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
from sklearn.ensemble import HistGradientBoostingClassifier  # noqa: E402
from sklearn.ensemble import HistGradientBoostingRegressor  # noqa: E402
from sklearn.isotonic import IsotonicRegression  # noqa: E402
from sklearn.metrics import brier_score_loss  # noqa: E402

HOURS = int(sys.argv[1]) if len(sys.argv) > 1 else 24
TOPN = int(sys.argv[2]) if len(sys.argv) > 2 else 24
STEP_MS = 15000
HORIZONS = [90000, 300000, 900000]
COST_BP = 6.0
FEATS = ["ofi_15s", "ofi_60s", "ofi_300s", "ret_15s_bp", "ret_60s_bp",
         "ret_300s_bp", "spread_bp", "depth_imb", "vwap_dev_bp", "vol_300s_bp"]


def main() -> int:
    now_ms = int(time.time() * 1000)
    lo_ms = now_ms - HOURS * 3600 * 1000
    results = []
    with psycopg.connect(_market_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT symbol, count(*) c FROM asterdex_trades WHERE event_ts_ms > %s"
            " GROUP BY symbol ORDER BY c DESC LIMIT %s", (lo_ms, TOPN))
        coins = [(r[0].replace("USDT", ""), int(r[1])) for r in cur.fetchall()]
        print(f"扫描 {len(coins)} 个最活跃币(近 {HOURS}h):"
              f" {[c for c, _ in coins[:12]]}")
        for sym, n_tr in coins:
            if n_tr < 300:
                continue
            cur.execute(
                "SELECT event_ts_ms, bid_px, bid_qty, ask_px, ask_qty FROM"
                " asterdex_book_ticker WHERE symbol=%s AND event_ts_ms > %s"
                " AND bid_px>0 AND ask_px>bid_px ORDER BY event_ts_ms",
                (sym + "USDT", lo_ms))
            bk = pd.DataFrame(cur.fetchall(), columns=["ts", "bp", "bq", "ap", "aq"])
            if len(bk) < 1000:
                continue
            bk = bk.astype({"bp": float, "bq": float, "ap": float, "aq": float})
            bk["mid"] = (bk["bp"] + bk["ap"]) / 2.0
            cur.execute(
                "SELECT event_ts_ms, price, qty, is_buyer_maker FROM asterdex_trades"
                " WHERE symbol=%s AND event_ts_ms > %s ORDER BY event_ts_ms",
                (sym + "USDT", lo_ms))
            tr = pd.DataFrame(cur.fetchall(), columns=["ts", "px", "qty", "bm"])
            tr = tr.astype({"px": float, "qty": float})
            tr["signed"] = np.where(tr["bm"], -tr["qty"] * tr["px"], tr["qty"] * tr["px"])
            grid = np.arange(lo_ms + 300000, now_ms - max(HORIZONS), STEP_MS)
            if len(grid) < 400:
                continue
            t = pd.DataFrame({"t": grid})
            m = pd.merge_asof(t, bk[["ts", "mid", "bp", "bq", "ap", "aq"]].rename(
                columns={"ts": "t"}), on="t", direction="backward")
            df = pd.DataFrame({"t": grid, "mid": m["mid"].values,
                               "bq": m["bq"].values, "aq": m["aq"].values})
            df["spread_bp"] = ((m["ap"].values - m["bp"].values)
                               / df["mid"].replace(0, np.nan) * 1e4)
            df["depth_imb"] = (df["bq"] - df["aq"]) / (df["bq"] + df["aq"] + 1e-9)
            for w_s, w_n in ((15, 1), (60, 4), (300, 20)):
                mw = pd.merge_asof(t - w_s * 1000, bk[["ts", "mid"]].rename(
                    columns={"ts": "t", "mid": "m_prev"}), on="t", direction="backward")
                df[f"ret_{w_s}s_bp"] = (df["mid"] / mw["m_prev"].values - 1.0) * 1e4
            sgn = tr.set_index("ts")["signed"]
            for w_s in (15, 60, 300):
                vals, gross = [], []
                for x in grid:
                    seg = sgn[(sgn.index >= x - w_s * 1000) & (sgn.index < x)]
                    g = float(tr.loc[(tr["ts"] >= x - w_s * 1000) & (tr["ts"] < x),
                                     "px"].mul(tr.loc[(tr["ts"] >= x - w_s * 1000)
                                                      & (tr["ts"] < x), "qty"]).sum())
                    vals.append(float(seg.sum()) / g if g > 0 else 0.0)
                    gross.append(g)
                df[f"ofi_{w_s}s"] = vals
            df["vwap_dev_bp"] = (df["mid"] / df["mid"].rolling(4, min_periods=1).mean()
                                 - 1.0) * 1e4
            df["vol_300s_bp"] = (df["mid"].pct_change(fill_method=None) * 1e4).rolling(
                20, min_periods=5).std()
            for hz in HORIZONS:
                d2 = df.copy()
                d2["target_bp"] = (d2["mid"].shift(-int(hz / STEP_MS)) - d2["mid"]) \
                    / d2["mid"] * 1e4
                d2 = d2.replace([np.inf, -np.inf], np.nan).dropna(
                    subset=FEATS + ["target_bp"])
                if len(d2) < 500:
                    continue
                split = int(len(d2) * 2 / 3)
                Xtr, Xte = d2[FEATS].iloc[:split], d2[FEATS].iloc[split:]
                ytr, yte = d2["target_bp"].iloc[:split], d2["target_bp"].iloc[split:]
                reg = HistGradientBoostingRegressor(
                    max_iter=120, learning_rate=0.07, max_depth=4,
                    min_samples_leaf=40, random_state=7).fit(Xtr, ytr)
                pred = reg.predict(Xte)
                sel = pred >= COST_BP
                # [h901] 回归样本 <20 也**给尽力值**,不写 None
                if sel.sum() >= 20:
                    e_reg = (float((yte[sel] - COST_BP).mean()), int(sel.sum()))
                else:
                    _vals = yte[sel] - COST_BP
                    e_reg = ((float(_vals.mean()) if len(_vals) else 0.0),
                             int(sel.sum()))
                clf = HistGradientBoostingClassifier(
                    max_iter=100, learning_rate=0.07, max_depth=4,
                    min_samples_leaf=40, random_state=7).fit(
                    Xtr, (ytr > 0).astype(int))
                praw = clf.predict_proba(Xte)[:, 1]
                iso = IsotonicRegression(out_of_bounds="clip").fit(
                    praw, (yte > 0).astype(int))
                pcal = iso.predict(praw)
                selc = pcal >= 0.55
                # [h901 用户"空值被说成证据不足?"] 校准样本 <20 也**给尽力值**:
                # 用现有校准样本直接算均值,不再写 None。
                if selc.sum() >= 20:
                    e_cal = (float((yte[selc] - COST_BP).mean()), int(selc.sum()))
                else:
                    _vals = yte[selc] - COST_BP
                    e_cal = ((float(_vals.mean()) if len(_vals) else 0.0),
                             int(selc.sum()))
                b = brier_score_loss((yte > 0).astype(int), praw)
                results.append({"symbol": sym, "hz_sec": int(hz / 1000),
                                "n": int(len(d2)),
                                "n_reg": e_reg[1], "edge_reg_bp": e_reg[0],
                                "n_cal": e_cal[1], "edge_cal_bp": e_cal[0],
                                "brier": round(float(b), 4)})
                print(f"  {sym:<10} {int(hz/1000):>4}s n={len(d2):>5} "
                      f"回归选{e_reg[1]:>3} {'n/a' if e_reg[0] is None else f'{e_reg[0]:+.2f}bp':>8} "
                      f"| 校准选{e_cal[1]:>3} "
                      f"{'n/a' if e_cal[0] is None else f'{e_cal[0]:+.2f}bp':>8} "
                      f"| Brier {b:.3f}")
    good = [r for r in results
            if (r["edge_cal_bp"] or 0) > 0 or (r["edge_reg_bp"] or 0) > 0]
    good.sort(key=lambda r: -max(r["edge_cal_bp"] or 0, r["edge_reg_bp"] or 0))
    print(f"\n== 正期望组合({len(good)}/{len(results)})== ")
    for r in good[:15]:
        print(f"  {r['symbol']:<10} {r['hz_sec']:>4}s 回归 {r['edge_reg_bp']} "
              f"(n={r['n_reg']}) 校准 {r['edge_cal_bp']} (n={r['n_cal']}) Brier {r['brier']}")
    (ROOT / "data" / "flow_edge_scan_last.json").write_text(
        json.dumps({"ts": time.time(), "hours": HOURS, "cost_bp": COST_BP,
                    "positive": good, "all": results},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print("  ✓ 已写 data/flow_edge_scan_last.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
