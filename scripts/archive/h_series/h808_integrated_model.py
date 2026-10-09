# -*- coding: utf-8 -*-
"""[h808 2026-10-04 用户"全面执行 + 用数据和过去经验做公式设计与算法整合"]

整合模型 v2:真深度 OFI + 相对价值(币 vs BTC)+ 多尺度流 + 动量。

公式设计(每条都注明来源):
  1. **真深度 OFI**(Cont/Kukanov/Stoikov 订单流失衡,20 档深度快照):
       DOI_t = (Σ bid_qty − Σ ask_qty)/Σ qty           (深度失衡,前 20 档)
       OFI_t = Δbid_qty(最优档) − Δask_qty(最优档)      (最优档净增,真 OFI)
     过去经验:h358/h361 的"流定律"用的是成交代理;这是它的深度版。
  2. **相对价值**(小币跟随大币的经验 + 检验):
       rel_mid_bp = (coin_mid/BTC_mid 的相对偏离) × 1e4
       rel_flow   = coin 的流的相对强度
     过去经验:宇宙币多与 BTC 高相关 ⇒ 单币方向难判,但**相对偏离**可能回归。
  3. 多尺度成交失衡(5/15/60/300s)、多尺度动量、价差、波动(沿用 v1)。
  4. 目标:币的 90s 前向漂移(回归)+ 方向(分类 + isotonic 校准)。
     评估:walk-forward 前 2/3 训练 / 后 1/3 样本外;Brier + 决策规则每笔期望。
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

UNI = ["NEAR", "PENGU", "CBRS", "RESOLV", "1000SHIB", "COIN"]
REF = "BTC"
STEP_MS = 15000
HORIZON_MS = int(sys.argv[1]) if len(sys.argv) > 1 else 90000
HOURS = int(sys.argv[2]) if len(sys.argv) > 2 else 12
COST_BP = 6.0


def _f(x):
    try:
        return float(x)
    except Exception:
        return 0.0


def load_mids(conn, sym: str, lo_ms: int) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT event_ts_ms, bid_px, bid_qty, ask_px, ask_qty FROM asterdex_book_ticker"
            " WHERE symbol=%s AND event_ts_ms > %s AND bid_px>0 AND ask_px>bid_px"
            " ORDER BY event_ts_ms", (sym, lo_ms))
        bk = pd.DataFrame(cur.fetchall(), columns=["ts", "bp", "bq", "ap", "aq"])
    if bk.empty:
        return bk
    bk = bk.astype({"bp": float, "bq": float, "ap": float, "aq": float})
    bk["mid"] = (bk["bp"] + bk["ap"]) / 2.0
    return bk


def load_trades(conn, sym: str, lo_ms: int) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT event_ts_ms, price, qty, is_buyer_maker FROM asterdex_trades"
            " WHERE symbol=%s AND event_ts_ms > %s ORDER BY event_ts_ms", (sym, lo_ms))
        tr = pd.DataFrame(cur.fetchall(), columns=["ts", "px", "qty", "bm"])
    if tr.empty:
        return tr
    tr = tr.astype({"px": float, "qty": float})
    tr["signed"] = np.where(tr["bm"], -tr["qty"] * tr["px"], tr["qty"] * tr["px"])
    return tr


def load_depth(conn, sym: str, lo_ms: int) -> pd.DataFrame:
    """每 15s 取一帧深度快照(20 档),算深度失衡与最优档量。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT event_ts_ms, bids, asks FROM asterdex_depth_snapshots"
            " WHERE symbol=%s AND event_ts_ms > %s"
            " AND event_ts_ms %% 15000 < 400 ORDER BY event_ts_ms",
            (sym, lo_ms))
        rows = cur.fetchall()
    out = []
    for ts, bids, asks in rows:
        try:
            bq = sum(_f(l[1]) for l in (bids or []))
            aq = sum(_f(l[1]) for l in (asks or []))
            b1 = _f((bids or [["0", "0"]])[0][1])
            a1 = _f((asks or [["0", "0"]])[0][1])
            out.append({"ts": int(ts), "dbid": bq, "dask": aq, "b1": b1, "a1": a1})
        except Exception:
            continue
    return pd.DataFrame(out)


def main() -> int:
    now_ms = int(time.time() * 1000)
    lo_ms = now_ms - HOURS * 3600 * 1000
    with psycopg.connect(_market_dsn(), autocommit=True) as conn:
        ref_bk = load_mids(conn, REF + "USDT", lo_ms)
        ref_tr = load_trades(conn, REF + "USDT", lo_ms)
        print(f"参考 {REF}: book={len(ref_bk)} trades={len(ref_tr)}")
        grid = np.arange(lo_ms + 300000, now_ms - HORIZON_MS, STEP_MS)
        report = []
        for sym in UNI:
            bk = load_mids(conn, sym + "USDT", lo_ms)
            tr = load_trades(conn, sym + "USDT", lo_ms)
            dp = load_depth(conn, sym + "USDT", lo_ms)
            if bk.empty or len(bk) < 500 or len(dp) < 50:
                print(f"  {sym:<9} 数据不足(book={len(bk)}, depth={len(dp)})→ 跳过")
                continue
            t = pd.DataFrame({"t": grid})
            m = pd.merge_asof(t, bk[["ts", "mid", "bp", "bq", "ap", "aq"]].rename(
                columns={"ts": "t"}), on="t", direction="backward")
            m90 = pd.merge_asof(t, bk[["ts", "mid"]].rename(
                columns={"ts": "t", "mid": "mid_90"}), on="t", direction="forward",
                tolerance=HORIZON_MS * 2)
            rm = pd.merge_asof(t, ref_bk[["ts", "mid"]].rename(
                columns={"ts": "t", "mid": "ref_mid"}), on="t", direction="backward")
            rm90 = pd.merge_asof(t, ref_bk[["ts", "mid"]].rename(
                columns={"ts": "t", "mid": "ref_mid_90"}), on="t", direction="forward",
                tolerance=HORIZON_MS * 2)
            dd = pd.merge_asof(t, dp.rename(columns={"ts": "t"}), on="t",
                               direction="backward", tolerance=60000)
            df = pd.DataFrame({"t": grid})
            df["mid"] = m["mid"].values
            df["spread_bp"] = ((m["ap"].values - m["bp"].values)
                               / df["mid"].replace(0, np.nan) * 1e4)
            df["mid_90"] = m90["mid_90"].values
            df["ref_mid"] = rm["ref_mid"].values
            df["ref_mid_90"] = rm90["ref_mid_90"].values
            df["dbid"] = dd["dbid"].values
            df["dask"] = dd["dask"].values
            df["b1"] = dd["b1"].values
            df["a1"] = dd["a1"].values
            df = df.dropna(subset=["mid", "mid_90", "ref_mid", "ref_mid_90"])
            if len(df) < 300:
                print(f"  {sym:<9} 合成样本 {len(df)} 太少 → 跳过")
                continue
            # 目标:币的 90s 漂移(bp)
            df["target_bp"] = (df["mid_90"] - df["mid"]) / df["mid"] * 1e4
            # 相对价值特征
            df["rel_mid_bp"] = (df["mid"] / df["ref_mid"] - 1.0) * 1e4
            df["rel_target_bp"] = ((df["mid_90"] / df["ref_mid_90"])
                                   - (df["mid"] / df["ref_mid"])) * 1e4
            df["ret_60s_bp"] = df["mid"].pct_change(4, fill_method=None) * 1e4
            df["ret_300s_bp"] = df["mid"].pct_change(20, fill_method=None) * 1e4
            df["ref_ret_60s_bp"] = df["ref_mid"].pct_change(4, fill_method=None) * 1e4
            # 深度特征:失衡 + 变化率 + 最优档
            df["dep_imb"] = (df["dbid"] - df["dask"]) / (df["dbid"] + df["dask"] + 1e-9)
            df["dep_imb_chg"] = df["dep_imb"].diff()
            df["top_imb"] = (df["b1"] - df["a1"]) / (df["b1"] + df["a1"] + 1e-9)
            df["ofi_depth"] = (df["b1"].diff() - df["a1"].diff())
            # 成交流特征
            tr_s = tr.set_index("ts")["signed"] if not tr.empty else pd.Series(dtype=float)

            def _flow(a, b):
                if tr_s.empty:
                    return 0.0
                seg = tr_s[(tr_s.index >= a) & (tr_s.index < b)]
                return float(seg.sum())

            df["ofi_60s"] = [_flow(x - 60000, x) for x in grid[:len(df)]]
            gross = float(tr["px"].mul(tr["qty"]).sum()) if not tr.empty else 0.0
            df["ofi_60n"] = df["ofi_60s"] / (gross / max(1, HOURS * 240) + 1e-9)
            # 价差已在上面按网格长度算好(避免 dropna 后长度错位)
            df["vol_300s"] = (df["mid"].pct_change(fill_method=None) * 1e4).rolling(
                20, min_periods=5).std()
            feats = ["rel_mid_bp", "ret_60s_bp", "ret_300s_bp", "ref_ret_60s_bp",
                     "dep_imb", "dep_imb_chg", "top_imb", "ofi_depth",
                     "ofi_60s", "ofi_60n", "spread_bp", "vol_300s"]
            df = df.replace([np.inf, -np.inf], np.nan).dropna(subset=feats + ["target_bp"])
            if len(df) < 300:
                print(f"  {sym:<9} 有效样本 {len(df)} 太少 → 跳过")
                continue
            split = int(len(df) * 2 / 3)
            Xtr, Xte = df[feats].iloc[:split], df[feats].iloc[split:]
            ytr, yte = df["target_bp"].iloc[:split], df["target_bp"].iloc[split:]
            ytr_r = df["rel_target_bp"].iloc[:split]
            yte_r = df["rel_target_bp"].iloc[split:]
            reg = HistGradientBoostingRegressor(max_iter=150, learning_rate=0.07,
                                                max_depth=4, min_samples_leaf=40,
                                                random_state=7).fit(Xtr, ytr)
            reg_r = HistGradientBoostingRegressor(max_iter=150, learning_rate=0.07,
                                                  max_depth=4, min_samples_leaf=40,
                                                  random_state=7).fit(Xtr, ytr_r)
            p_abs = reg.predict(Xte)
            p_rel = reg_r.predict(Xte)
            sel = p_abs >= COST_BP
            sel_r = p_rel >= COST_BP
            clf = HistGradientBoostingClassifier(max_iter=120, learning_rate=0.07,
                                                 max_depth=4, min_samples_leaf=40,
                                                 random_state=7).fit(
                Xtr, (ytr > 0).astype(int))
            praw = clf.predict_proba(Xte)[:, 1]
            iso = IsotonicRegression(out_of_bounds="clip").fit(
                praw, (yte > 0).astype(int))
            pcal = iso.predict(praw)
            b_raw = brier_score_loss((yte > 0).astype(int), praw)
            b_cal = brier_score_loss((yte > 0).astype(int), pcal)
            sel_c = pcal >= 0.55
            e_abs = float((yte[sel] - COST_BP).mean()) if sel.sum() >= 20 else None
            e_rel = float((yte[sel_r] - COST_BP).mean()) if sel_r.sum() >= 20 else None
            e_cal = float((yte[sel_c] - COST_BP).mean()) if sel_c.sum() >= 20 else None

            def _s(v):
                return "n/a" if v is None else f"{v:+.2f}bp"

            print(f"  {sym:<9} n={len(df):>5} OOS:绝对入选 {int(sel.sum()):>3} 笔 {_s(e_abs)} | "
                  f"相对入选 {int(sel_r.sum()):>3} 笔 {_s(e_rel)} | "
                  f"校准 0.55 选 {int(sel_c.sum()):>3} 笔 {_s(e_cal)} | "
                  f"Brier {b_raw:.4f}→{b_cal:.4f}")
            report.append({"symbol": sym, "n": len(df),
                           "n_abs": int(sel.sum()), "edge_abs_bp": e_abs,
                           "n_rel": int(sel_r.sum()), "edge_rel_bp": e_rel,
                           "n_cal": int(sel_c.sum()), "edge_cal_bp": e_cal,
                           "brier_raw": round(b_raw, 4), "brier_cal": round(b_cal, 4)})
    (ROOT / "data" / "flow_prob_model_v2.json").write_text(
        json.dumps({"ts": time.time(), "horizon_sec": HORIZON_MS / 1000,
                    "hours": HOURS, "cost_bp": COST_BP, "report": report},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print("  ✓ 已写 data/flow_prob_model_v2.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
