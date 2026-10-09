# -*- coding: utf-8 -*-
"""[h894 2026-10-07] 高频趋势概率模型 —— 训练侧(numpy/sklearn,离线 .venv 跑)。

从 asterdex_trades + asterdex_book_ticker 直接构造每秒特征 + 30s 前向中价收益标签,
训练「加权朴素贝叶斯 + Platt 校准」的二分类器一对(P_up / P_dn),
模型落 JSON 供 worker 的纯标准库推理侧(trend_prob.py)读取。

特征口径与 worker 逐字对齐(见 trend_prob.FEATURES 注释);分桶边界/权重/校准
全部从训练数据学出,不写死。

诚实纪律:
  · 时间顺序切分(前 70% 学表,后 30% 校准+评估),不洗牌 ⇒ 无未来泄漏;
  · 指标全部样本外报告(Brier/AUC/可靠性分桶),校准不达标在报告里直说;
  · 门槛 p_min = 基础率 + margin(默认 0.08,与 CYCLE_PROB_GATE_MARGIN 同理念)。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from backend.services.market_maker.trend_prob import FEATURES

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")


# ══════════════════════════════════════════════════════════════════════
# 1. 数据装载:每秒特征 + 30s 前向标签
# ══════════════════════════════════════════════════════════════════════
def load_training_rows(symbols: List[str], hours: float = 24.0,
                       step_s: float = 2.0, horizon_s: float = 30.0,
                       ) -> Dict[str, Dict[str, np.ndarray]]:
    """[h899 v2] 每币返回 {obs(N×9, 列序=FEATURES), fwd(N,)} —— fwd 单位 bp。

    特征语义与 worker **逐字一致**(训练/推理同源):
      mp_skew_bp = 顶档微价偏离(microprice vs 中价),bp
      ofi        = 60s 主动买/卖量失衡               ← worker 的 _ofi_60s 同口径
      obi_top    = 顶档盘口失衡 (bid_qty0−ask_qty0)/(和)   ← book_ticker 顶档
      obi_top5   = 前 5 档盘口失衡(Σbid−Σask)/(Σ)        ← depth_snapshots
      trend_Ns   = (mid_t / mid_{t−N} − 1)×1e4        ← trend_move_bp 同口径
      accel      = trend_20s − trend_60s(加速度)
      vol_20s    = 近 20 步中价对数收益 std×1e4        ← realized_vol_bp 同口径
      fwd        = (mid_{t+horizon} / mid_t − 1)×1e4
    """
    from backend.services.market_maker.attribution import _market_dsn
    import psycopg

    t0 = time.time() - hours * 3600
    out: Dict[str, Dict[str, np.ndarray]] = {}
    with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
        for sym in symbols:
            cur.execute(
                "SELECT event_ts_ms/1000.0, price, qty, is_buyer_maker"
                " FROM asterdex_trades WHERE symbol=%s AND event_ts_ms>%s"
                " ORDER BY event_ts_ms", (sym, int(t0 * 1000)))
            tr = cur.fetchall()
            cur.execute(
                "SELECT event_ts_ms/1000.0, bid_px, ask_px, bid_qty, ask_qty"
                " FROM asterdex_book_ticker WHERE symbol=%s AND bid_px>0"
                " AND event_ts_ms>%s ORDER BY event_ts_ms",
                (sym, int(t0 * 1000)))
            bk = cur.fetchall()
            if len(tr) < 500 or len(bk) < 2000:
                continue
            tts = np.array([float(r[0]) for r in tr])
            qty = np.array([float(r[2]) for r in tr])
            is_buy = np.array([not r[3] for r in tr])
            bts = np.array([float(r[0]) for r in bk])
            bid = np.array([float(r[1]) for r in bk])
            ask = np.array([float(r[2]) for r in bk])
            bq = np.array([float(r[3]) for r in bk])
            aq = np.array([float(r[4]) for r in bk])
            mid = (bid + ask) / 2.0
            with np.errstate(divide="ignore", invalid="ignore"):
                mp = (bid * aq + ask * bq) / np.maximum(bq + aq, 1e-12)
            mp_skew_all = (mp - mid) / np.maximum(mid, 1e-12) * 1e4
            # 顶档盘口失衡(book_ticker 顶档量,免费)
            obi_top_all = (bq - aq) / np.maximum(bq + aq, 1e-12)

            t_start = max(tts[0], bts[0]) + 130
            t_end = min(tts[-1], bts[-1]) - horizon_s - 5
            grid = np.arange(t_start, t_end, step_s)
            mid_idx = np.searchsorted(bts, grid, side="right") - 1
            mid_g = mid[mid_idx]
            mp_g = mp_skew_all[mid_idx]
            obi_top_g = obi_top_all[mid_idx]
            fwd_idx = np.searchsorted(bts, grid + horizon_s, side="right") - 1
            fwd = (mid[fwd_idx] / mid_g - 1.0) * 1e4

            def _trend(n_s: float) -> np.ndarray:
                j = np.searchsorted(bts, grid - n_s, side="right") - 1
                j = np.maximum(j, 0)
                return (mid_g / mid[j] - 1.0) * 1e4

            t20, t60, t120 = _trend(20), _trend(60), _trend(120)
            accel = t20 - t60
            logm = np.log(np.maximum(mid_g, 1e-12))
            r1 = np.diff(logm, prepend=logm[0])
            vol20 = np.zeros_like(logm)
            for i in range(len(logm)):
                lo = max(0, i - 20)
                vol20[i] = float(np.std(r1[lo:i + 1])) * 1e4 if i > lo else 0.0

            buy_vol = np.where(is_buy, qty, 0.0)
            sell_vol = np.where(is_buy, 0.0, qty)
            cb = np.concatenate([[0.0], np.cumsum(buy_vol)])
            cs = np.concatenate([[0.0], np.cumsum(sell_vol)])
            hi = np.searchsorted(tts, grid, side="right")
            lo60 = np.searchsorted(tts, grid - 60.0, side="left")
            bv = cb[hi] - cb[lo60]
            sv = cs[hi] - cs[lo60]
            ofi60 = (bv - sv) / np.maximum(bv + sv, 1e-12)
            # [2026-10-08 提准] 订单流加强两个新特征:
            #   ofi_accel = 近 20s OFI − 近 60s OFI(资金在加速还是减速,领先反转)
            #   ofi_volw  = OFI × log1p(60s 成交量)(量大的 OFI 才可信,量小是噪音)
            lo20 = np.searchsorted(tts, grid - 20.0, side="left")
            bv20 = cb[hi] - cb[lo20]
            sv20 = cs[hi] - cs[lo20]
            ofi20 = (bv20 - sv20) / np.maximum(bv20 + sv20, 1e-12)
            ofi_accel = ofi20 - ofi60
            vol60 = bv + sv
            ofi_volw = ofi60 * np.log1p(np.maximum(vol60, 0.0))
            obs = np.zeros((len(grid), len(FEATURES)), dtype=np.float32)
            obs[:, 0] = mp_g
            obs[:, 1] = ofi60
            obs[:, 2] = obi_top_g
            obs[:, 3] = t20
            obs[:, 4] = t60
            obs[:, 5] = t120
            obs[:, 6] = accel
            obs[:, 7] = vol20
            obs[:, 8] = ofi_accel
            obs[:, 9] = ofi_volw
            ok = np.isfinite(obs).all(axis=1) & np.isfinite(fwd) & (mid_g > 0)
            out[sym] = {"obs": obs[ok], "fwd": fwd[ok].astype(np.float32)}
    return out


# ══════════════════════════════════════════════════════════════════════
# 2. 训练:加权朴素贝叶斯 + Platt 校准
# ══════════════════════════════════════════════════════════════════════
def _nb_tables(x: np.ndarray, y: np.ndarray, edges: np.ndarray
               ) -> Tuple[np.ndarray, float]:
    """P(bin|y=1)/P(bin|y=0) 的 log 比(拉普拉斯平滑)。返回 (ll[bins], MI)。

    [h899 修 MI 公式 bug] 旧 MI 写法 `p_bin·p_pos·log(p_pos/(p_bin·p1))` 对
    **退化特征**(常量列,全落同一桶)会算出虚假的大 MI ⇒ 权重被垃圾特征抢走
    (实测:常量列 w=1.0 压过真信号 ofi)。改为**规范的联合互信息**:
        MI = Σ_b Σ_y P(b,y)·log(P(b,y)/(P(b)·P(y)))
    常量列 P(b,y)=P(y)、P(b)=1 ⇒ 每项 log(1)=0 ⇒ MI=0 ✓(不携带标签信息)。
    """
    b = np.digitize(x, edges)
    n_bins = len(edges) + 1
    ll = np.zeros(n_bins)
    pos = y == 1
    n_pos, n_neg = int(pos.sum()), int((~pos).sum())
    n = max(1, len(y))
    p1 = float(pos.mean()) + 1e-12
    p0 = 1.0 - p1
    mi = 0.0
    for k in range(n_bins):
        in_b = (b == k)
        p_bin = float(in_b.mean()) + 1e-12
        p_given_pos = (float((in_b & pos).sum()) + 1.0) / (n_pos + n_bins)
        p_given_neg = (float((in_b & ~pos).sum()) + 1.0) / (n_neg + n_bins)
        ll[k] = np.log(p_given_pos / p_given_neg)
        # 规范联合互信息(用未平滑的联合频率,避免平滑项污染)
        p_b_pos = float((in_b & pos).sum()) / n       # P(b, y=1)
        p_b_neg = float((in_b & ~pos).sum()) / n      # P(b, y=0)
        if p_b_pos > 0:
            mi += p_b_pos * np.log(p_b_pos / (p_bin * p1) + 1e-12)
        if p_b_neg > 0:
            mi += p_b_neg * np.log(p_b_neg / (p_bin * p0) + 1e-12)
    return ll, max(0.0, mi)


def _platt(s: np.ndarray, y: np.ndarray, iters: int = 300, lr: float = 0.05
           ) -> Tuple[float, float]:
    """1 维 logistic:p = sigmoid(a*s + b)。手写梯度下降(免 sklearn 依赖版本差)。"""
    a, b = 1.0, 0.0
    y = y.astype(np.float64)
    for _ in range(iters):
        z = np.clip(a * s + b, -30, 30)
        p = 1.0 / (1.0 + np.exp(-z))
        g_a = float(((p - y) * s).mean())
        g_b = float((p - y).mean())
        a -= lr * g_a
        b -= lr * g_b
    return float(a), float(b)


def _metrics(p: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    brier = float(np.mean((p - y) ** 2))
    try:
        from sklearn.metrics import roc_auc_score
        auc = float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 else None
    except Exception:
        auc = None
    # 可靠性:5 分桶的 预测均值 vs 实际频率
    rel = []
    qs = np.quantile(p, [0.2, 0.4, 0.6, 0.8])
    b = np.digitize(p, qs)
    for k in range(5):
        m = b == k
        if m.any():
            rel.append({"pred": round(float(p[m].mean()), 4),
                        "actual": round(float(y[m].mean()), 4),
                        "n": int(m.sum())})
    return {"brier": round(brier, 5), "auc": (round(auc, 4) if auc else None),
            "reliability": rel}


def train_model(rows: Dict[str, Dict[str, np.ndarray]], label_thr_bp: float = 3.0,
                margin: float = 0.08, horizon_s: float = 30.0,
                ) -> Dict[str, Any]:
    """时间顺序 70/30 切分;返回可落 JSON 的模型字典(含样本外指标)。"""
    obs_all = np.concatenate([r["obs"] for r in rows.values()], axis=0)
    fwd_all = np.concatenate([r["fwd"] for r in rows.values()], axis=0)
    # 各币内部已按时间有序;拼接后按"每币 70/30"切,保时间序
    tr_idx: List[int] = []
    te_idx: List[int] = []
    off = 0
    for r in rows.values():
        n = len(r["fwd"])
        cut = int(n * 0.7)
        tr_idx.extend(range(off, off + cut))
        te_idx.extend(range(off + cut, off + n))
        off += n
    tr_idx = np.array(tr_idx)
    te_idx = np.array(te_idx)
    x_tr, f_tr = obs_all[tr_idx], fwd_all[tr_idx]
    x_te, f_te = obs_all[te_idx], fwd_all[te_idx]
    y_tr_up = (f_tr >= label_thr_bp).astype(np.int64)
    y_tr_dn = (f_tr <= -label_thr_bp).astype(np.int64)
    y_te_up = (f_te >= label_thr_bp).astype(np.int64)
    y_te_dn = (f_te <= -label_thr_bp).astype(np.int64)
    base_up, base_dn = float(y_tr_up.mean()), float(y_tr_dn.mean())

    tables: Dict[str, Any] = {}
    mis: List[float] = []
    raw: List[Tuple[np.ndarray, np.ndarray, np.ndarray, float]] = []
    for j, fname in enumerate(FEATURES):
        edges = np.quantile(x_tr[:, j], [0.2, 0.4, 0.6, 0.8])
        ll_up, mi_up = _nb_tables(x_tr[:, j], y_tr_up, edges)
        ll_dn, mi_dn = _nb_tables(x_tr[:, j], y_tr_dn, edges)
        mi = mi_up + mi_dn
        mis.append(mi)
        raw.append((edges, ll_up, ll_dn, mi))
    mi_max = max(mis) if max(mis) > 0 else 1.0
    for j, fname in enumerate(FEATURES):
        edges, ll_up, ll_dn, mi = raw[j]
        tables[fname] = {
            "edges": [round(float(e), 6) for e in edges],
            "w": round(float(mi / mi_max), 4) if mi_max > 0 else 0.0,
            "ll_up": [round(float(v), 6) for v in ll_up],
            "ll_dn": [round(float(v), 6) for v in ll_dn],
        }

    def _prior_odds(y: np.ndarray) -> float:
        p = float(y.mean())
        p = min(max(p, 1e-4), 1 - 1e-4)
        return float(np.log(p / (1 - p)))

    def _score(x: np.ndarray, side: str) -> np.ndarray:
        s = np.full(len(x), _prior_odds(y_tr_up if side == "up" else y_tr_dn))
        for j, fname in enumerate(FEATURES):
            t = tables[fname]
            b = np.digitize(x[:, j], np.array(t["edges"]))
            s = s + t["w"] * np.array(t["ll_up" if side == "up" else "ll_dn"])[b]
        return s

    s_te_up = _score(x_te, "up")
    s_te_dn = _score(x_te, "dn")
    pa, pb = _platt(s_te_up, y_te_up)
    da, db = _platt(s_te_dn, y_te_dn)
    p_te_up = 1.0 / (1.0 + np.exp(-np.clip(pa * s_te_up + pb, -30, 30)))
    p_te_dn = 1.0 / (1.0 + np.exp(-np.clip(da * s_te_dn + db, -30, 30)))

    # ── [h902 幅度预测] 岭回归预测**绝对**前向收益 E[|fwd_bp|](分析层加强)──
    # 方向由 NB 分类答"涨/跌";幅度答"预计动多大"(不管方向)。
    # 注意:带符号 fwd 几乎不可测(IC≈0.02),但 |fwd|(波动幅度)可测得多——
    # 波动是持续的。只在预计大动静时进场 ⇒ 赢单变大,治"赚小"的病根。
    mag_w: List[float] = []
    mag_b = 0.0
    mag_ic = None
    try:
        from sklearn.linear_model import Ridge
        abs_tr = np.abs(f_tr)          # 预测 |fwd|(幅度,比带符号好测得多)
        abs_te = np.abs(f_te)
        mu = x_tr.mean(axis=0)
        sd = x_tr.std(axis=0) + 1e-9
        xtr_n = (x_tr - mu) / sd
        xte_n = (x_te - mu) / sd
        ridge = Ridge(alpha=1.0)
        ridge.fit(xtr_n, abs_tr)
        pred_te = ridge.predict(xte_n)
        if len(np.unique(pred_te)) > 1 and len(np.unique(abs_te)) > 1:
            mag_ic = float(np.corrcoef(pred_te, abs_te)[0, 1])
        w_norm = ridge.coef_ / sd
        b_norm = float(ridge.intercept_ - (mu / sd * ridge.coef_).sum())
        mag_w = [round(float(w), 8) for w in w_norm]
        mag_b = round(b_norm, 8)
    except Exception:
        mag_w, mag_b, mag_ic = [], 0.0, None

    return {
        "version": 1,
        "trained_at": time.time(),
        "horizon_sec": horizon_s,
        "label_thr_bp": label_thr_bp,
        "features": list(FEATURES),
        "tables": tables,
        "prior": {"up": round(_prior_odds(y_tr_up), 6),
                  "dn": round(_prior_odds(y_tr_dn), 6)},
        "platt": {"up": [round(pa, 6), round(pb, 6)],
                  "dn": [round(da, 6), round(db, 6)]},
        "gate": {"p_min_up": round(base_up + margin, 4),
                 "p_min_dn": round(base_dn + margin, 4),
                 "margin": margin},
        # [h902] 幅度预测(带符号预期前向收益 bp)
        "magnitude": {"w": mag_w, "b": mag_b, "oos_ic": mag_ic},
        "metrics": {
            "n_train": int(len(tr_idx)), "n_test": int(len(te_idx)),
            "base_up": round(base_up, 4), "base_dn": round(base_dn, 4),
            "up": _metrics(p_te_up, y_te_up),
            "dn": _metrics(p_te_dn, y_te_dn),
            "mag_oos_ic": (round(mag_ic, 4) if mag_ic is not None else None),
        },
    }


def save_model(model: Dict[str, Any], path: Optional[Path] = None) -> Path:
    p = path or (ROOT / "data" / "hft_trend_prob" / "model.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(model, ensure_ascii=False, indent=1), encoding="utf-8")
    return p
