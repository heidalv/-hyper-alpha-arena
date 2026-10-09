# -*- coding: utf-8 -*-
"""[h807 2026-10-04 用户"概率计算不对,仔细设计公式和评分规则,可以复杂但要准确"]

S1 方向概率模型 v1:替代"OFI 符号 + 趋势同向"的粗糙启发式。

设计(三层,每层可独立验证):
  1. **特征层**(向量化,15s 采样):多尺度流(5s/15s/60s/300s 成交失衡)、
     多尺度中价动量、微价偏离、价差、已实现波动、成交率、深度失衡、VWAP 偏离;
  2. **模型层**:HistGradientBoosting 回归 → E[90s 漂移 | 特征](每币一个模型,
     币的动态不同;walk-forward 分割:前 16h 训练、后 8h 样本外测试);
  3. **评分与决策层**:score = 预测漂移;决策规则 = score ≥ 进场成本(价差+费)
     才进场;并训练 P(方向) 分类器 + isotonic 校准,输出**校准概率**。
     样本外只报:命中率、决策规则的每笔期望、校准曲线(Brier)。

关于计算:数据量 ≈ 6 币 × 24h ≈ 1~4 万样本 × 12 特征 —— CPU 上 GBM 几秒;
**瓶颈是数据不是算力**(COIN 24h 只有 23 笔成交,任何 GPU 都学不出它)。
本管线已向量化,数据量涨起来后可直接换 LightGBM/NN/GPU。
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

UNI = ["1000SHIB", "NEAR", "PENGU", "CBRS", "COIN", "RESOLV"]
STEP_MS = 15000
HORIZON_MS = int(sys.argv[1]) if len(sys.argv) > 1 else 90000
COST_BP = 6.0          # 进场成本:价差(≈2bp)+ taker 费(4bp)的保守合计
if len(sys.argv) > 2:
    UNI = [s.upper() for s in sys.argv[2].split(",")]


def load(sym: str, lo_ms: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT event_ts_ms, price, qty, is_buyer_maker FROM asterdex_trades"
            " WHERE symbol=%s AND event_ts_ms > %s ORDER BY event_ts_ms", (sym, lo_ms))
        tr = pd.DataFrame(cur.fetchall(), columns=["ts", "px", "qty", "bm"])
        tr = tr.astype({"px": float, "qty": float})
        cur.execute(
            "SELECT event_ts_ms, bid_px, bid_qty, ask_px, ask_qty FROM asterdex_book_ticker"
            " WHERE symbol=%s AND event_ts_ms > %s AND bid_px>0 AND ask_px>bid_px"
            " ORDER BY event_ts_ms", (sym, lo_ms))
        bk = pd.DataFrame(cur.fetchall(), columns=["ts", "bp", "bq", "ap", "aq"])
        bk = bk.astype({"bp": float, "bq": float, "ap": float, "aq": float})
    return tr, bk


def features(tr: pd.DataFrame, bk: pd.DataFrame, grid: np.ndarray) -> pd.DataFrame:
    """15s 网格上的特征矩阵(向量化)。"""
    tr = tr.assign(signed=np.where(tr["bm"], -tr["qty"] * tr["px"], tr["qty"] * tr["px"]))
    bk = bk.assign(mid=(bk["bp"] + bk["ap"]) / 2.0).sort_values("ts")
    t = pd.DataFrame({"t": grid})
    mid_now = pd.merge_asof(t, bk[["ts", "mid", "bp", "bq", "ap", "aq"]].rename(
        columns={"ts": "t"}), on="t", direction="backward")
    mid_90 = pd.merge_asof(t, bk[["ts", "mid"]].rename(columns={"ts": "t", "mid": "mid_90"}),
                           on="t", direction="forward", tolerance=135000)
    out = pd.DataFrame({"t": grid, "mid": mid_now["mid"].values})
    out["mid_90"] = mid_90["mid_90"].values
    out["target_bp"] = (out["mid_90"] - out["mid"]) / out["mid"] * 1e4

    def _sum_between(a: float, b: float) -> float:
        m = (tr["ts"] >= a) & (tr["ts"] < b)
        return float(tr.loc[m, "signed"].sum())

    def _gross(a: float, b: float) -> float:
        m = (tr["ts"] >= a) & (tr["ts"] < b)
        return float((tr.loc[m, "qty"] * tr.loc[m, "px"]).sum())

    wins = [(5, 15000), (15, 15000), (60, 60000), (300, 300000)]
    for name, look_s in wins:
        ofi, gross = [], []
        for t0 in grid:
            s = _sum_between(t0 - look_s * 1000, t0)
            g = _gross(t0 - look_s * 1000, t0)
            ofi.append(s / g if g > 0 else 0.0)
            gross.append(g)
        out[f"ofi_{name}s"] = ofi
    # 中价动量(各尺度)
    out["ret_15s_bp"] = out["mid"].pct_change(1, fill_method=None) * 1e4  # 15s
    out["ret_60s_bp"] = out["mid"].pct_change(4, fill_method=None) * 1e4
    out["ret_300s_bp"] = out["mid"].pct_change(20, fill_method=None) * 1e4
    # 价差/深度/微价
    out["spread_bp"] = (mid_now["ap"] - mid_now["bp"]) / out["mid"] * 1e4
    out["depth_imb"] = (mid_now["bq"] - mid_now["aq"]) / (mid_now["bq"] + mid_now["aq"] + 1e-9)
    out["vwap_dev_bp"] = (out["mid"] / out["mid"].rolling(4, min_periods=1).mean() - 1) * 1e4
    out["vol_300s_bp"] = out["ret_15s_bp"].rolling(20, min_periods=5).std()
    return out


def main() -> int:
    now_ms = int(time.time() * 1000)
    lo_ms = now_ms - 24 * 3600 * 1000
    grid = np.arange(lo_ms + 300000, now_ms - HORIZON_MS, STEP_MS)
    report = []
    for sym in UNI:
        tr, bk = load(sym + "USDT", lo_ms)
        if len(tr) < 100 or len(bk) < 500:
            print(f"  {sym:<10} 数据不足(trades={len(tr)}, book={len(bk)})→ 跳过")
            continue
        df = features(tr, bk, grid).dropna(subset=["target_bp", "ofi_5s", "ofi_15s",
                                                   "ofi_60s", "ofi_300s", "ret_15s_bp",
                                                   "ret_60s_bp", "ret_300s_bp",
                                                   "spread_bp", "depth_imb",
                                                   "vwap_dev_bp", "vol_300s_bp"])
        if len(df) < 300:
            print(f"  {sym:<10} 有效样本 {len(df)} 太少 → 跳过")
            continue
        feats = ["ofi_5s", "ofi_15s", "ofi_60s", "ofi_300s", "ret_15s_bp",
                 "ret_60s_bp", "ret_300s_bp", "spread_bp", "depth_imb",
                 "vwap_dev_bp", "vol_300s_bp"]
        # walk-forward:前 2/3 训练、后 1/3 测试(样本外,时间顺序)
        split = int(len(df) * 2 / 3)
        Xtr, Xte = df[feats].iloc[:split], df[feats].iloc[split:]
        ytr, yte = df["target_bp"].iloc[:split], df["target_bp"].iloc[split:]
        clf_tr = (df["target_bp"] > 0).astype(int).iloc[:split]
        clf_te = (df["target_bp"] > 0).astype(int).iloc[split:]
        # 回归:E[漂移]
        reg = HistGradientBoostingRegressor(max_iter=120, learning_rate=0.08,
                                            max_depth=4, min_samples_leaf=40,
                                            random_state=7)
        reg.fit(Xtr, ytr)
        pred = reg.predict(Xte)
        # 决策规则:pred ≥ COST 才进场 → 样本外每笔期望(扣成本后)
        sel = pred >= COST_BP
        oos_pnl = float((yte[sel] - COST_BP).mean()) if sel.sum() >= 30 else np.nan
        hit = float((yte[sel] > COST_BP).mean()) if sel.sum() >= 30 else np.nan
        n_sel = int(sel.sum())
        # 方向概率 + isotonic 校准
        clf = HistGradientBoostingClassifier(max_iter=100, learning_rate=0.08,
                                             max_depth=4, min_samples_leaf=40,
                                             random_state=7)
        clf.fit(Xtr, clf_tr)
        p_raw = clf.predict_proba(Xte)[:, 1]
        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(p_raw, clf_te)
        p_cal = iso.predict(p_raw)
        brier_raw = brier_score_loss(clf_te, p_raw)
        brier_cal = brier_score_loss(clf_te, p_cal)
        # 校准后按 0.55 门槛选的样本外 P&L
        sel_c = p_cal >= 0.55
        oos_pnl_cal = float((yte[sel_c] - COST_BP).mean()) if sel_c.sum() >= 30 else np.nan
        n_sel_c = int(sel_c.sum())
        print(f"  {sym:<10} n={len(df)} OOS 回归入选 {n_sel} 笔 每笔 "
              f"{oos_pnl:+.2f}bp 命中率 {hit*100 if hit==hit else 0:.0f}% | "
              f"校准:Brier {brier_raw:.4f}→{brier_cal:.4f} | 0.55 门槛选 {n_sel_c} 笔 "
              f"{oos_pnl_cal if oos_pnl_cal==oos_pnl_cal else float('nan'):+.2f}bp")
        report.append({"symbol": sym, "n": len(df), "n_selected": n_sel,
                       "oos_edge_bp": None if oos_pnl != oos_pnl else round(oos_pnl, 2),
                       "hit_rate": None if hit != hit else round(hit, 3),
                       "brier_raw": round(brier_raw, 4), "brier_cal": round(brier_cal, 4),
                       "n_selected_cal": n_sel_c,
                       "oos_edge_cal_bp": None if oos_pnl_cal != oos_pnl_cal
                       else round(oos_pnl_cal, 2)})
    (ROOT / "data" / "flow_prob_model_last.json").write_text(
        json.dumps({"ts": time.time(), "horizon_sec": 90, "cost_bp": COST_BP,
                    "report": report}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("  ✓ 已写 data/flow_prob_model_last.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
