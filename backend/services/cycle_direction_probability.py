"""周期方向概率引擎 —— Cycle Direction Probability Engine。

定位
----
在现有"三周期 agent"（short/mid/long）之上，补齐一块**结构化、可校准的方向概率**能力：
给定某周期的技术特征，输出该周期未来走势为 涨 / 跌 / 震荡 的概率，并给出一个
可用于门禁与仲裁的"校准质量"标量。

为什么用条件频率 / 加权朴素贝叶斯（而非黑盒 ML）
------------------------------------------------
1. **可解释**：每个方向概率都能拆解到"哪些特征、哪个分桶、贡献多少 log-odds"，
   契合本项目 `agent_evidence_builder` / `agent_fact_guard` 的证据链纪律。
2. **便宜**：查表 + 少量乘加，热路径可随每个 tick 调用，不增加 LLM 成本。
3. **可校准**：训练时做时间序列 train/test 切分，产出 Brier score + reliability，
   知道"这个 tier 的概率到底准不准"，据此在门禁/仲裁里给它加权或降权。
4. **数据自证方向**：实证发现短周期呈均值回归（动量类信号反向），长周期偏趋势跟随；
   条件频率表直接从历史学到这个符号，无需人工写死"RSI>55 就看多"。

数据流
------
    训练（离线，train_and_save）：
        crypto_klines → 逐根特征 + 未来 N 根方向标签
        → 每 tier 一张朴素贝叶斯概率表（prior + P(bucket|dir)）
        → 时间切分做 Brier/reliability 校准
        → data/cycle_prob/prob_model_<tier>.json

    推理（在线，engine.estimate）：
        runtime indicators / features → 分桶 → 加权朴素贝叶斯后验
        → CycleProbResult(prob_up/down/range, direction, confidence, calibration_quality, drivers)

本模块**不下单、不调 LLM**；只做概率估计。门禁/仲裁/证据注入在各自模块调用本引擎。
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ─────────────────── 常量：周期、特征、分桶 ───────────────────

# 三周期 → 主周期（须与 backend/config/tier_timeframe_map.py 一致）
TIER_PRIMARY: Dict[str, str] = {"short": "15m", "mid": "1h", "long": "4h"}

# 每个 tier 的方向定义窗口（未来多少根主周期 K 线）
TIER_FORWARD_BARS: Dict[str, int] = {"short": 8, "mid": 6, "long": 6}

# 方向标签索引
DIR_UP, DIR_DOWN, DIR_RANGE = 0, 1, 2
DIR_NAMES = {DIR_UP: "up", DIR_DOWN: "down", DIR_RANGE: "range"}

# 参与建模的特征（scale-free 或已归一，便于跨币跨周期共用同一套分桶）
FEATURES: List[str] = [
    "adx", "di_diff", "ema_align", "rsi", "macd_sign",
    "atr_pct", "vol_ratio", "mom", "hh_hl",
]

# 连续特征的分桶边界（左开右闭 digitize）；离散特征单列处理
BUCKET_EDGES: Dict[str, List[float]] = {
    "adx": [15, 25, 40],
    "di_diff": [-15, -5, 5, 15],
    "rsi": [30, 45, 55, 70],
    "atr_pct": [0.005, 0.012, 0.025, 0.05],
    "vol_ratio": [0.7, 1.0, 1.5, 2.5],
    "mom": [-0.03, -0.005, 0.005, 0.03],
}
# 离散特征取值 → 桶号
DISCRETE_BUCKETS: Dict[str, Dict[int, int]] = {
    "ema_align": {-1: 0, 0: 1, 1: 2},
    "macd_sign": {-1: 0, 0: 1, 1: 2},
    "hh_hl": {-2: 0, -1: 1, 0: 2, 1: 3, 2: 4},
}

_MODEL_DIR = Path(__file__).resolve().parents[2] / "data" / "cycle_prob"
_LAPLACE = 1.0  # 拉普拉斯平滑


# ─────────────────── 向量化技术指标（本模块为单一权威来源）───────────────────

def ema_series(arr: np.ndarray, period: int) -> np.ndarray:
    n = len(arr)
    out = np.full(n, np.nan)
    if n < period:
        return out
    alpha = 2.0 / (period + 1)
    out[period - 1] = np.mean(arr[:period])
    for i in range(period, n):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
    return out


def rsi_series(close: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(close)
    out = np.full(n, np.nan)
    if n <= period:
        return out
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = float(np.mean(gain[:period]))
    avg_loss = float(np.mean(loss[:period]))
    for i in range(period, n):
        avg_gain = (avg_gain * (period - 1) + gain[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + loss[i - 1]) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1 + avg_gain / avg_loss)
    return out


def atr_series(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(close)
    out = np.full(n, np.nan)
    if n <= period:
        return out
    tr = np.zeros(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    atr = float(np.mean(tr[1:period + 1]))
    out[period] = atr
    for i in range(period + 1, n):
        atr = (atr * (period - 1) + tr[i]) / period
        out[i] = atr
    return out


def adx_series(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14
               ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """返回 (adx, plus_di, minus_di) 逐点序列（Wilder 平滑）。"""
    n = len(close)
    adx = np.full(n, np.nan)
    p_di = np.full(n, np.nan)
    m_di = np.full(n, np.nan)
    if n <= 2 * period:
        return adx, p_di, m_di
    up_move = high[1:] - high[:-1]
    down_move = low[:-1] - low[1:]
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = np.zeros(n - 1)
    for i in range(1, n):
        tr[i - 1] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))

    def _wilder(x: np.ndarray) -> np.ndarray:
        s = np.full(len(x), np.nan)
        if len(x) < period:
            return s
        s[period - 1] = float(np.sum(x[:period]))
        for i in range(period, len(x)):
            s[i] = s[i - 1] - s[i - 1] / period + x[i]
        return s

    tr_s, pdm_s, mdm_s = _wilder(tr), _wilder(plus_dm), _wilder(minus_dm)
    dx = np.full(n - 1, np.nan)
    for i in range(period - 1, n - 1):
        if tr_s[i] and tr_s[i] > 1e-9 and not math.isnan(tr_s[i]):
            pdi = 100.0 * pdm_s[i] / tr_s[i]
            mdi = 100.0 * mdm_s[i] / tr_s[i]
            p_di[i + 1], m_di[i + 1] = pdi, mdi
            denom = pdi + mdi
            dx[i] = 100.0 * abs(pdi - mdi) / denom if denom > 1e-9 else 0.0
    first = 2 * period - 1
    if first < n - 1:
        seg = dx[period - 1:first + 1]
        seg = seg[~np.isnan(seg)]
        if len(seg) >= 1:
            cur = float(np.mean(seg))
            adx[first + 1] = cur
            for i in range(first + 1, n - 1):
                if not math.isnan(dx[i]):
                    cur = (cur * (period - 1) + dx[i]) / period
                    adx[i + 1] = cur
    return adx, p_di, m_di


def hh_hl_series(high: np.ndarray, low: np.ndarray, lookback: int = 20, pivot: int = 5) -> np.ndarray:
    n = len(high)
    out = np.zeros(n)
    for t in range(lookback, n):
        hs, ls = high[t - lookback:t], low[t - lookback:t]
        hh = hl = lh = ll = 0
        for i in range(pivot, lookback, pivot):
            if np.max(hs[i:min(i + pivot, lookback)]) > np.max(hs[max(0, i - pivot):i]):
                hh += 1
            else:
                lh += 1
            if np.min(ls[i:min(i + pivot, lookback)]) > np.min(ls[max(0, i - pivot):i]):
                hl += 1
            else:
                ll += 1
        out[t] = (1 if hh >= 2 else 0) + (1 if hl >= 2 else 0) - (1 if lh >= 2 else 0) - (1 if ll >= 2 else 0)
    return out


def build_feature_series(klines: Dict[str, np.ndarray]) -> Dict[str, np.ndarray]:
    """从 OHLCV 数组构建逐根特征序列（训练用）。klines: {high, low, close, volume}。"""
    h, l, c, v = klines["high"], klines["low"], klines["close"], klines["volume"]
    n = len(c)
    adx, pdi, mdi = adx_series(h, l, c)
    ema9, ema21, ema50 = ema_series(c, 9), ema_series(c, 21), ema_series(c, 50)
    rsi = rsi_series(c, 14)
    atr = atr_series(h, l, c, 14)
    ema12, ema26 = ema_series(c, 12), ema_series(c, 26)
    macd_line = ema12 - ema26
    macd_signal = ema_series(np.nan_to_num(macd_line), 9)
    macd_hist = macd_line - macd_signal

    ema_align = np.zeros(n)
    for i in range(n):
        if not (math.isnan(ema9[i]) or math.isnan(ema21[i]) or math.isnan(ema50[i])):
            if ema9[i] > ema21[i] > ema50[i]:
                ema_align[i] = 1.0
            elif ema9[i] < ema21[i] < ema50[i]:
                ema_align[i] = -1.0

    macd_sign = np.where(macd_hist > 0, 1.0, np.where(macd_hist < 0, -1.0, 0.0))
    macd_sign = np.where(np.isnan(macd_hist), np.nan, macd_sign)

    atr_pct = np.where(c > 0, atr / c, np.nan)
    vol_sma = np.full(n, np.nan)
    for i in range(20, n):
        m = float(np.mean(v[i - 20:i]))
        vol_sma[i] = v[i] / m if m > 0 else np.nan
    mom = np.full(n, np.nan)
    for i in range(10, n):
        mom[i] = (c[i] - c[i - 10]) / c[i - 10] if c[i - 10] > 0 else np.nan
    hh_hl = hh_hl_series(h, l)

    return {
        "adx": adx, "di_diff": pdi - mdi, "ema_align": ema_align, "rsi": rsi,
        "macd_sign": macd_sign, "atr_pct": atr_pct, "vol_ratio": vol_sma,
        "mom": mom, "hh_hl": hh_hl,
    }


def forward_return_series(close: np.ndarray, n_ahead: int) -> np.ndarray:
    n = len(close)
    out = np.full(n, np.nan)
    for i in range(n - n_ahead):
        if close[i] > 0:
            out[i] = (close[i + n_ahead] - close[i]) / close[i]
    return out


# ─────────────────── 分桶 ───────────────────

def bucketize(feature: str, value: Optional[float]) -> Optional[int]:
    """把特征值映射到桶号；无法映射返回 None。"""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(v):
        return None
    if feature in DISCRETE_BUCKETS:
        key = int(round(v))
        key = max(min(key, max(DISCRETE_BUCKETS[feature])), min(DISCRETE_BUCKETS[feature]))
        return DISCRETE_BUCKETS[feature].get(key, DISCRETE_BUCKETS[feature][min(DISCRETE_BUCKETS[feature])])
    edges = BUCKET_EDGES.get(feature)
    if edges is None:
        return None
    return int(np.digitize([v], edges)[0])


def _n_buckets(feature: str) -> int:
    if feature in DISCRETE_BUCKETS:
        return len(set(DISCRETE_BUCKETS[feature].values()))
    return len(BUCKET_EDGES[feature]) + 1


# ─────────────────── 概率模型 ───────────────────

@dataclass
class ProbModel:
    tier: str
    timeframe: str
    forward_bars: int
    prior: List[float]                                  # [P(up), P(down), P(range)]
    likelihood: Dict[str, List[List[float]]]            # feature → bucket → [P(b|up),P(b|down),P(b|range)]
    feature_weights: Dict[str, float]                   # 特征权重（来自互信息，已归一）
    range_threshold: float
    calibration: Dict[str, float]                       # brier / reliability / accuracy / quality
    meta: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "tier": self.tier, "timeframe": self.timeframe, "forward_bars": self.forward_bars,
            "prior": self.prior, "likelihood": self.likelihood,
            "feature_weights": self.feature_weights, "range_threshold": self.range_threshold,
            "calibration": self.calibration, "meta": self.meta,
        }

    @staticmethod
    def from_dict(d: Dict) -> "ProbModel":
        return ProbModel(
            tier=d["tier"], timeframe=d["timeframe"], forward_bars=d["forward_bars"],
            prior=d["prior"], likelihood=d["likelihood"],
            feature_weights=d.get("feature_weights", {}), range_threshold=d.get("range_threshold", 0.0),
            calibration=d.get("calibration", {}), meta=d.get("meta", {}),
        )


@dataclass
class CycleProbResult:
    available: bool
    tier: str
    prob_up: float = 1 / 3
    prob_down: float = 1 / 3
    prob_range: float = 1 / 3
    direction: str = "neutral"       # up / down / range / neutral
    confidence: float = 0.0          # = max(prob) 相对随机基线的强度，0~1
    calibration_quality: float = 0.0  # 训练期校准质量 0~1（差=不可信）
    top_drivers: List[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "available": self.available, "tier": self.tier,
            "prob_up": round(self.prob_up, 4), "prob_down": round(self.prob_down, 4),
            "prob_range": round(self.prob_range, 4), "direction": self.direction,
            "confidence": round(self.confidence, 4),
            "calibration_quality": round(self.calibration_quality, 4),
            "top_drivers": self.top_drivers, "reason": self.reason,
        }


# ─────────────────── 训练 ───────────────────

def _label_from_fwd(fwd: np.ndarray, thr: float) -> np.ndarray:
    lab = np.full(len(fwd), np.nan)
    lab[fwd > thr] = DIR_UP
    lab[fwd < -thr] = DIR_DOWN
    lab[(fwd >= -thr) & (fwd <= thr)] = DIR_RANGE
    return lab


def _mutual_info(bucket: np.ndarray, label: np.ndarray) -> float:
    mask = ~(np.isnan(bucket) | np.isnan(label))
    b, ell = bucket[mask], label[mask]
    if len(b) < 60:
        return 0.0
    total = len(b)
    mi = 0.0
    for bv in np.unique(b):
        pb = np.mean(b == bv)
        for lv in np.unique(ell):
            pl = np.mean(ell == lv)
            pbl = np.mean((b == bv) & (ell == lv))
            if pbl > 0 and pb > 0 and pl > 0:
                mi += pbl * math.log2(pbl / (pb * pl))
    return float(max(0.0, mi))


def _build_tables(feat_buckets: Dict[str, np.ndarray], label: np.ndarray
                  ) -> Tuple[List[float], Dict[str, List[List[float]]], Dict[str, float]]:
    """从（分桶后的特征 + 标签）构建 prior + likelihood + 特征权重。"""
    valid = ~np.isnan(label)
    lab = label[valid].astype(int)
    counts = np.array([np.sum(lab == d) for d in (DIR_UP, DIR_DOWN, DIR_RANGE)], dtype=float)
    prior = ((counts + _LAPLACE) / (counts.sum() + 3 * _LAPLACE)).tolist()

    likelihood: Dict[str, List[List[float]]] = {}
    weights: Dict[str, float] = {}
    for f in FEATURES:
        nb = _n_buckets(f)
        fb = feat_buckets[f]
        # P(bucket | dir)：对每个方向，桶分布（含拉普拉斯平滑）
        table = [[_LAPLACE] * 3 for _ in range(nb)]
        fb_valid = fb[valid]
        for b, d in zip(fb_valid, lab):
            if b is None or (isinstance(b, float) and math.isnan(b)):
                continue
            bi = int(b)
            if 0 <= bi < nb:
                table[bi][d] += 1.0
        # 归一为 P(bucket|dir)（按列，即每个方向下各桶概率和为 1）
        col_sums = [sum(table[bi][d] for bi in range(nb)) for d in range(3)]
        norm = [[table[bi][d] / col_sums[d] if col_sums[d] > 0 else 1.0 / nb
                 for d in range(3)] for bi in range(nb)]
        likelihood[f] = norm
        weights[f] = _mutual_info(fb.astype(float), label)

    w_sum = sum(weights.values())
    if w_sum > 0:
        weights = {k: v / w_sum for k, v in weights.items()}
    else:
        weights = {k: 1.0 / len(FEATURES) for k in FEATURES}
    return prior, likelihood, weights


def _predict_row(model_prior: List[float], likelihood: Dict[str, List[List[float]]],
                 weights: Dict[str, float], buckets: Dict[str, Optional[int]]) -> np.ndarray:
    """加权朴素贝叶斯后验（log 域），返回 [P(up),P(down),P(range)]。"""
    log_post = np.log(np.array(model_prior) + 1e-9)
    for f, b in buckets.items():
        if b is None:
            continue
        tbl = likelihood.get(f)
        w = weights.get(f, 0.0)
        if not tbl or w <= 0 or not (0 <= b < len(tbl)):
            continue
        row = tbl[b]
        log_post += w * np.log(np.array(row) + 1e-9)
    log_post -= log_post.max()
    post = np.exp(log_post)
    s = post.sum()
    return post / s if s > 0 else np.array([1 / 3, 1 / 3, 1 / 3])


def _calibrate(prior, likelihood, weights, feat_buckets_test: Dict[str, np.ndarray],
               label_test: np.ndarray) -> Dict[str, float]:
    """在测试集上评估 Brier / reliability / 三态准确率，并折算校准质量。"""
    valid = ~np.isnan(label_test)
    idx = np.where(valid)[0]
    if len(idx) < 50:
        return {"brier": None, "reliability": None, "accuracy": None,
                "quality": 0.3, "n_test": int(len(idx)), "note": "样本不足"}
    briers = []
    correct = 0
    conf_bins: Dict[int, List[int]] = {}
    for i in idx:
        buckets = {f: (None if math.isnan(feat_buckets_test[f][i]) else int(feat_buckets_test[f][i]))
                   for f in FEATURES}
        post = _predict_row(prior, likelihood, weights, buckets)
        d = int(label_test[i])
        onehot = np.zeros(3)
        onehot[d] = 1.0
        briers.append(float(np.sum((post - onehot) ** 2)))
        pred = int(np.argmax(post))
        if pred == d:
            correct += 1
        bin_id = min(9, int(post[pred] * 10))
        conf_bins.setdefault(bin_id, [0, 0])
        conf_bins[bin_id][0] += 1
        conf_bins[bin_id][1] += 1 if pred == d else 0

    brier = float(np.mean(briers))
    accuracy = correct / len(idx)
    # reliability（ECE）：各置信桶 |平均置信 - 命中率| 加权
    ece = 0.0
    for bin_id, (cnt, hit) in conf_bins.items():
        if cnt == 0:
            continue
        conf_mid = (bin_id + 0.5) / 10.0
        acc = hit / cnt
        ece += (cnt / len(idx)) * abs(conf_mid - acc)
    # 三态随机基线 Brier ≈ 0.667；quality = 相对基线的改善，clamp [0,1]
    baseline_brier = 0.667
    quality = max(0.0, min(1.0, (baseline_brier - brier) / baseline_brier * 2.0))
    # 校准差（ECE 大）再打折
    quality *= max(0.3, 1.0 - ece)
    return {"brier": round(brier, 4), "reliability_ece": round(ece, 4),
            "accuracy": round(accuracy, 4), "quality": round(quality, 4),
            "n_test": int(len(idx))}


def train_tier(tier: str, klines_list: List[Dict[str, np.ndarray]],
               test_frac: float = 0.3) -> Optional[ProbModel]:
    """用多币历史训练某 tier 的概率模型（时间序列切分校准）。"""
    tf = TIER_PRIMARY[tier]
    n_ahead = TIER_FORWARD_BARS[tier]

    train_feat: Dict[str, List[np.ndarray]] = {f: [] for f in FEATURES}
    train_lab: List[np.ndarray] = []
    test_feat: Dict[str, List[np.ndarray]] = {f: [] for f in FEATURES}
    test_lab: List[np.ndarray] = []
    all_fwd: List[np.ndarray] = []

    # 先扫一遍确定 range 阈值（全样本 |收益| 中位数的一半）
    per_symbol = []
    for k in klines_list:
        if len(k["close"]) < 80:
            continue
        feats = build_feature_series(k)
        fwd = forward_return_series(k["close"], n_ahead)
        per_symbol.append((feats, fwd, len(k["close"])))
        all_fwd.append(fwd)
    if not per_symbol:
        return None
    fwd_cat = np.concatenate(all_fwd)
    fwd_valid = fwd_cat[~np.isnan(fwd_cat)]
    if len(fwd_valid) < 200:
        return None
    range_thr = 0.5 * float(np.median(np.abs(fwd_valid)))

    for feats, fwd, n in per_symbol:
        split = int(n * (1 - test_frac))
        lab = _label_from_fwd(fwd, range_thr)
        for f in FEATURES:
            fb = np.array([bucketize(f, feats[f][i]) if not math.isnan(feats[f][i]) else np.nan
                           for i in range(n)], dtype=float)
            train_feat[f].append(fb[:split])
            test_feat[f].append(fb[split:])
        train_lab.append(lab[:split])
        test_lab.append(lab[split:])

    tr_feat = {f: np.concatenate(train_feat[f]) for f in FEATURES}
    tr_lab = np.concatenate(train_lab)
    te_feat = {f: np.concatenate(test_feat[f]) for f in FEATURES}
    te_lab = np.concatenate(test_lab)

    prior, likelihood, weights = _build_tables(tr_feat, tr_lab)
    calib = _calibrate(prior, likelihood, weights, te_feat, te_lab)

    return ProbModel(
        tier=tier, timeframe=tf, forward_bars=n_ahead, prior=prior,
        likelihood=likelihood, feature_weights=weights, range_threshold=range_thr,
        calibration=calib,
        meta={"trained_at": time.time(), "n_symbols": len(per_symbol),
              "n_train": int(np.sum(~np.isnan(tr_lab)))},
    )


def save_model(model: ProbModel) -> Path:
    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    path = _MODEL_DIR / f"prob_model_{model.tier}.json"
    path.write_text(json.dumps(model.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ─────────────────── 在线推理引擎（单例）───────────────────

class CycleProbabilityEngine:
    """加载已训练概率模型，提供在线 estimate。线程安全、磁盘热加载。"""

    def __init__(self) -> None:
        self._models: Dict[str, ProbModel] = {}
        self._mtimes: Dict[str, float] = {}
        self._lock = threading.Lock()

    def _load_if_needed(self, tier: str) -> Optional[ProbModel]:
        path = _MODEL_DIR / f"prob_model_{tier}.json"
        if not path.exists():
            return None
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return self._models.get(tier)
        with self._lock:
            if tier not in self._models or self._mtimes.get(tier) != mtime:
                try:
                    self._models[tier] = ProbModel.from_dict(
                        json.loads(path.read_text(encoding="utf-8")))
                    self._mtimes[tier] = mtime
                    logger.info("[CycleProb] 已加载 %s 概率模型 (calib_quality=%s)",
                                tier, self._models[tier].calibration.get("quality"))
                except Exception as exc:
                    logger.warning("[CycleProb] 加载 %s 模型失败: %s", tier, exc)
                    return None
            return self._models.get(tier)

    def is_ready(self, tier: str) -> bool:
        return self._load_if_needed(tier) is not None

    def estimate(self, tier: str, features: Dict[str, Optional[float]]) -> CycleProbResult:
        """给定特征字典估计方向概率。features 键为 FEATURES 子集，缺失项自动忽略。"""
        tier = tier if tier in TIER_PRIMARY else "mid"
        model = self._load_if_needed(tier)
        if model is None:
            return CycleProbResult(available=False, tier=tier, reason="模型未训练/未加载")

        buckets: Dict[str, Optional[int]] = {}
        used: List[Tuple[str, float]] = []
        for f in FEATURES:
            b = bucketize(f, features.get(f))
            buckets[f] = b
            if b is not None:
                used.append((f, model.feature_weights.get(f, 0.0)))
        if not any(b is not None for b in buckets.values()):
            return CycleProbResult(available=False, tier=tier, reason="无可用特征")

        post = _predict_row(model.prior, model.likelihood, model.feature_weights, buckets)
        p_up, p_down, p_range = float(post[0]), float(post[1]), float(post[2])
        top = sorted(used, key=lambda x: x[1], reverse=True)[:3]
        drivers = [f for f, _ in top]

        arg = int(np.argmax(post))
        direction = DIR_NAMES[arg]
        # confidence：最大后验相对随机基线 1/3 的强度，归一到 0~1
        confidence = max(0.0, min(1.0, (float(post.max()) - 1 / 3) / (1 - 1 / 3)))
        quality = float(model.calibration.get("quality") or 0.0)

        return CycleProbResult(
            available=True, tier=tier, prob_up=p_up, prob_down=p_down, prob_range=p_range,
            direction=direction, confidence=confidence, calibration_quality=quality,
            top_drivers=drivers,
            reason=f"{direction} p={post.max():.2f} q={quality:.2f} drivers={','.join(drivers)}",
        )

    def estimate_from_indicators(self, tier: str, indicators: Dict) -> CycleProbResult:
        """从 runtime indicators dict（market_envs[symbol].indicators_<tf>）提取特征并估计。"""
        feats = extract_features_from_indicators(indicators)
        return self.estimate(tier, feats)


# ─────────────────── runtime 特征提取 ───────────────────

def extract_features_from_indicators(indicators: Optional[Dict]) -> Dict[str, Optional[float]]:
    """把运行时 indicators dict 映射为引擎特征字典（缺失字段留 None）。

    indicators 来源：unified_data_pool 的 indicators[symbol] 扁平 dict，或
    market_envs[symbol].indicators_<tf> 分块 dict。字段名尽量做多别名兼容。
    """
    if not isinstance(indicators, dict):
        return {}

    def g(*keys):
        for k in keys:
            if k in indicators and indicators[k] is not None:
                return indicators[k]
        return None

    feats: Dict[str, Optional[float]] = {}
    feats["adx"] = _to_float(g("adx", "adx_1h", "adx_4h"))
    pdi, mdi = _to_float(g("plus_di", "plus_di_4h")), _to_float(g("minus_di", "minus_di_4h"))
    feats["di_diff"] = (pdi - mdi) if (pdi is not None and mdi is not None) else None
    feats["rsi"] = _to_float(g("rsi", "rsi_1h", "rsi_4h"))
    feats["vol_ratio"] = _to_float(g("vol_ratio", "volume_ratio"))

    # ema_align：优先 ema9/21/50，退回 ema_trend 符号
    e9, e21, e50 = _to_float(g("ema_9", "ema9")), _to_float(g("ema_21", "ema21")), _to_float(g("ema_50", "ema50"))
    if e9 is not None and e21 is not None and e50 is not None:
        feats["ema_align"] = 1.0 if e9 > e21 > e50 else (-1.0 if e9 < e21 < e50 else 0.0)
    else:
        et = g("ema_trend")
        if isinstance(et, str):
            feats["ema_align"] = 1.0 if "bull" in et else (-1.0 if "bear" in et else 0.0)
        else:
            etf = _to_float(et)
            feats["ema_align"] = (1.0 if etf > 0.001 else (-1.0 if etf < -0.001 else 0.0)) if etf is not None else None

    mh = _to_float(g("macd_hist", "macd_histogram", "macd"))
    feats["macd_sign"] = (1.0 if mh > 0 else (-1.0 if mh < 0 else 0.0)) if mh is not None else None

    atr, close = _to_float(g("atr", "atr_1h", "atr_4h")), _to_float(g("close", "price"))
    atr_pct = _to_float(g("atr_pct"))
    if atr_pct is None and atr is not None and close and close > 0:
        atr_pct = atr / close
    feats["atr_pct"] = atr_pct

    mom = _to_float(g("mom", "momentum", "price_change_1h"))
    if mom is not None and abs(mom) > 1.0:  # 若是百分比数值（如 2.5 表示 2.5%）
        mom = mom / 100.0
    feats["mom"] = mom

    feats["hh_hl"] = _to_float(g("hh_hl"))
    return feats


def extract_tier_features_from_snapshot(indicators: Optional[Dict], tier: str) -> Dict[str, Optional[float]]:
    """从 unified_data_pool 的扁平 indicators[symbol] 按 tier 主周期取字段。

    字段命名约定：
      - mid(1h)  → 基础字段（rsi, adx, ema_9/21/50, macd_hist, atr, vol_ratio, plus_di, minus_di）
      - long(4h) → `_4h` 后缀字段
      - short(15m/5m) → `short_` 前缀字段（字段较少，引擎自动忽略缺失项）
    """
    if not isinstance(indicators, dict):
        return {}
    if tier == "long":
        sub = {
            "adx": indicators.get("adx_4h"), "plus_di": indicators.get("plus_di_4h"),
            "minus_di": indicators.get("minus_di_4h"), "rsi": indicators.get("rsi_4h"),
            "ema_9": indicators.get("ema_9_4h"), "ema_21": indicators.get("ema_21_4h"),
            "ema_50": indicators.get("ema_50_4h"), "macd_hist": indicators.get("macd_hist_4h"),
            "atr": indicators.get("atr_4h"), "close": indicators.get("close") or indicators.get("price"),
            "vol_ratio": indicators.get("vol_ratio_4h") or indicators.get("vol_ratio"),
            "ema_trend": indicators.get("ema_trend_4h") or indicators.get("ema_trend"),
        }
        return extract_features_from_indicators(sub)
    if tier == "short":
        sub = {
            "rsi": indicators.get("short_rsi") or indicators.get("rsi"),
            "macd_hist": indicators.get("short_macd_hist"),
            "ema_trend": indicators.get("short_ema_trend"),
            "atr": indicators.get("atr"), "close": indicators.get("close") or indicators.get("price"),
            "vol_ratio": indicators.get("vol_ratio"),
        }
        return extract_features_from_indicators(sub)
    # mid → 基础字段
    return extract_features_from_indicators(indicators)


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


# 全局单例
cycle_probability_engine = CycleProbabilityEngine()


# ─────────────────── 离线训练入口（从数据库拉历史）───────────────────

DEFAULT_TRAIN_SYMBOLS = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "LINK",
    "WIF", "NEAR", "UNI", "CRV", "ONDO", "XLM", "WLD", "ZEC",
]


def _psycopg_dsn() -> str:
    raw = os.environ.get(
        "MARKET_DATABASE_URL",
        "postgresql://db_admin:YOUR_DB_PASSWORD@localhost:5432/alpha_market",
    )
    return raw.replace("postgresql+psycopg://", "postgresql://")


def _load_klines_from_db(cur, symbol: str, period: str) -> Optional[Dict[str, np.ndarray]]:
    """取行数最多交易所的全量历史 K 线。"""
    cur.execute(
        "SELECT exchange, count(*) c FROM crypto_klines WHERE symbol=%s AND period=%s "
        "GROUP BY exchange ORDER BY c DESC LIMIT 1",
        (symbol.upper(), period),
    )
    row = cur.fetchone()
    if not row:
        return None
    exch = row[0]
    cur.execute(
        "SELECT high_price, low_price, close_price, volume FROM crypto_klines "
        "WHERE symbol=%s AND period=%s AND exchange=%s ORDER BY timestamp ASC",
        (symbol.upper(), period, exch),
    )
    rows = cur.fetchall()
    if not rows or len(rows) < 80:
        return None
    return {
        "high": np.array([float(r[0] or 0) for r in rows]),
        "low": np.array([float(r[1] or 0) for r in rows]),
        "close": np.array([float(r[2] or 0) for r in rows]),
        "volume": np.array([float(r[3] or 0) for r in rows]),
    }


def train_and_save_all(symbols: Optional[List[str]] = None, dsn: Optional[str] = None
                       ) -> Dict[str, Optional[Dict]]:
    """从数据库拉历史，训练并保存 short/mid/long 三张概率表。返回各 tier 校准摘要。"""
    try:
        import psycopg
    except ImportError:
        logger.error("[CycleProb] 缺少 psycopg，无法训练")
        return {}

    symbols = symbols or DEFAULT_TRAIN_SYMBOLS
    dsn = dsn or _psycopg_dsn()
    out: Dict[str, Optional[Dict]] = {}

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for tier, tf in TIER_PRIMARY.items():
                klines_list = []
                for sym in symbols:
                    k = _load_klines_from_db(cur, sym, tf)
                    if k:
                        klines_list.append(k)
                if not klines_list:
                    logger.warning("[CycleProb] %s(%s) 无训练数据", tier, tf)
                    out[tier] = None
                    continue
                model = train_tier(tier, klines_list)
                if model is None:
                    out[tier] = None
                    continue
                path = save_model(model)
                out[tier] = {"path": str(path), "calibration": model.calibration,
                             "n_symbols": model.meta.get("n_symbols")}
                logger.info("[CycleProb] %s 训练完成 → %s (%s)", tier, path, model.calibration)
    # 训练后同步校准质量到自适应门槛（低优先级，不覆盖人工/进化意图）
    try:
        out["_governor_sync"] = sync_calibration_to_governor()
    except Exception as exc:
        logger.warning("[CycleProb] 训练后 governor 同步失败: %s", exc)
    return out


def sync_calibration_to_governor() -> Dict[str, object]:
    """把各 tier 的校准质量映射为 RuntimeGovernor 自适应意图（低优先级 source）。

    逻辑：short tier（对应 scalp）方向历史越难预测（校准质量越低），
    越应提高短线开仓置信门槛（scalp_min_confidence），减少在"看不清方向"时
    的短线裸奔；校准转好则可小幅回落。source=cycle_prob_calibration 未注册于
    SOURCE_PRIORITY，自动落到最低优先级(30)，不会覆盖 manual/opencode/feedback/进化。
    """
    result: Dict[str, object] = {}
    try:
        from backend.services.runtime_governor import runtime_governor
        from backend.config.settings import V5_SCALP_MIN_CONFIDENCE
    except Exception as exc:
        logger.warning("[CycleProb] governor 同步跳过: %s", exc)
        return result

    engine = cycle_probability_engine
    model = engine._load_if_needed("short")
    if model is None:
        return result
    q = float(model.calibration.get("quality") or 0.0)
    base = int(V5_SCALP_MIN_CONFIDENCE)

    if q < 0.10:
        target = min(90, base + 5)      # 方向几乎不可预测 → 收紧
        reason = f"short方向校准质量低({q:.2f})，收紧短线置信门槛"
    elif q >= 0.25:
        target = max(60, base - 2)      # 有一定edge → 轻微放宽
        reason = f"short方向校准质量较好({q:.2f})，小幅放宽"
    else:
        # 中间区间不干预，让既有意图自然过期
        return {"skipped": True, "quality": q}

    try:
        patch = runtime_governor.submit_intent(
            "scalp_min_confidence", int(target),
            source="cycle_prob_calibration", confidence=min(0.5, 0.2 + q),
            reason=reason, ttl_sec=3 * 86400,
        )
        result = {"key": "scalp_min_confidence", "target": target, "quality": q, "patch": patch}
        logger.info("[CycleProb] governor 同步: scalp_min_confidence→%s (%s)", target, reason)
    except Exception as exc:
        logger.warning("[CycleProb] submit_intent 失败: %s", exc)
    return result


def _main() -> int:
    import argparse
    try:
        sys_stdout = __import__("sys").stdout
        sys_stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="周期方向概率引擎 —— 离线训练")
    ap.add_argument("--symbols", type=str, default="")
    args = ap.parse_args()
    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or None
    res = train_and_save_all(symbols=syms)
    for tier, info in res.items():
        if tier.startswith("_"):
            continue
        if info:
            print(f"[{tier}] calib={info['calibration']}  币数={info['n_symbols']}  → {info['path']}")
        else:
            print(f"[{tier}] 训练失败/数据不足")
    if res.get("_governor_sync"):
        print(f"[governor] {res['_governor_sync']}")
    return 0 if any(v for k, v in res.items() if not k.startswith("_")) else 1


if __name__ == "__main__":
    import sys as _sys
    # 允许 `python -m backend.services.cycle_direction_probability` 或直接运行
    if __package__ in (None, ""):
        _root = Path(__file__).resolve().parents[2]
        if str(_root) not in _sys.path:
            _sys.path.insert(0, str(_root))
        try:
            from dotenv import load_dotenv
            load_dotenv(_root / ".env", override=False)
        except Exception:
            pass
    _sys.exit(_main())
