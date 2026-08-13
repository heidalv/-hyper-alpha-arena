"""
M2 因子工厂 · 标签流水线（设计骨架）

对应《短期因子策略全链路详细技术设计.md》§2.1/2.3。
本模块只提供接口与纯函数，不接入任何现有调度/进化链路；
由 FEATURE_FACTOR_LABELS_ENABLED 控制是否被未来调用方启用（默认 false）。

纯函数可直接单测：
- build_triple_barrier_labels : 包装 labeling/triple_barrier.apply_triple_barrier
- net_ic / turnover / capacity_usd : 净IC/换手/容量估算
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

# [2026-08-13 P1-5] 三重障碍标签默认启用（短线 5m/15m 挖矿目标贴近 SL/TP 结算）；
# 回滚：FEATURE_FACTOR_LABELS_ENABLED=0|false|off
FEATURE_FACTOR_LABELS_ENABLED = os.getenv("FEATURE_FACTOR_LABELS_ENABLED", "true").lower() in (
    "1", "true", "yes", "on",
)


@dataclass
class FactorLabelConfig:
    """标签参数（默认值来自设计文档 §2.1，经网格小验证固化；env 可覆盖）。"""
    horizon_bars: int = 12        # 5m 因子默认 12 根（1h）
    vol_mult: float = float(os.getenv("FEATURE_FACTOR_LABELS_VOL_MULT", "1.5") or 1.5)   # 障碍 = 波动率 × vol_mult
    min_vol: float = float(os.getenv("FEATURE_FACTOR_LABELS_MIN_VOL", "0.0001") or 0.0001)  # 波动率下限，过低跳过
    vol_lookback: int = 20


@dataclass
class FactorQualityMetrics:
    """因子质量指标（设计文档 §2.3）。"""
    ic_mean: float = 0.0
    icir: float = 0.0
    net_ic: float = 0.0
    turnover: float = 0.0
    capacity_usd: float = 0.0


def build_triple_barrier_labels(
    df: pd.DataFrame,
    horizon_bars: int = 12,
    vol_mult: float = 1.5,
    min_vol: float = 0.0001,
) -> pd.Series:
    """把 OHLCV 序列转成 -1/0/+1 的三重障碍标签（对齐 df.index）。

    包装 backend.services.labeling.triple_barrier.apply_triple_barrier；
    失败时返回全 0 空标签（fail-safe，调用方应检查非空）。
    """
    empty = pd.Series(0, index=df.index, dtype=int)
    if df is None or df.empty or "close" not in df.columns:
        return empty
    try:
        from backend.services.labeling.triple_barrier import (
            BarrierLabel,
            TripleBarrierConfig,
            apply_triple_barrier,
        )
        prices = df["close"].astype(float)
        cfg = TripleBarrierConfig(
            num_days=horizon_bars,
            upper_mult=vol_mult,
            lower_mult=vol_mult,
            min_vol=min_vol,
            vol_lookback=20,
        )
        records = apply_triple_barrier(prices, events_index=prices.index, config=cfg)
        labels = {}
        for idx_t, row in records:
            if isinstance(row, dict):
                label = row.get("label", BarrierLabel.VERTICAL)
            else:
                label = getattr(row, "label", BarrierLabel.VERTICAL)
            if label == BarrierLabel.UPPER:
                labels[idx_t] = 1
            elif label == BarrierLabel.LOWER:
                labels[idx_t] = -1
            else:
                labels[idx_t] = 0
        out = pd.Series(labels, dtype=int)
        return out.reindex(df.index).fillna(0).astype(int)
    except Exception:
        return empty


def meta_label_samples(
    factor_series: pd.Series,
    labels: pd.Series,
    features: dict,
    horizon_bars: int = 12,
) -> pd.DataFrame:
    """meta-labeling 样本生成：粗信号触发事件集 → 二分类训练 DataFrame。

    在粗信号（|factor_series|>0）触发的事件上，用 5-8 个特征
    （ATR%、量比、盘口失衡、OI delta 等）对齐「该不该交易」标签（labels>0 → win=1）。

    Parameters:
        factor_series: 粗信号分数（确定事件集 + 保留 signal 列）。
        labels: 三重障碍/收益标签（>0 视为 win）。
        features: {特征名: Series}，必须与 factor_series 同索引。
        horizon_bars: 标签前瞻期（透传语义，构造侧不重算）。

    Returns:
        DataFrame：列 = [signal, <features...>, win]，仅含有效触发事件行。
    """
    if factor_series is None or labels is None or not features:
        return pd.DataFrame()
    try:
        sig = pd.Series(factor_series).astype(float)
        lab = pd.Series(labels).astype(float)
        idx = sig.index.intersection(lab.index)
        if len(idx) == 0:
            return pd.DataFrame()
        cols = {"signal": sig.reindex(idx)}
        for name, fs in features.items():
            if isinstance(fs, pd.Series):
                cols[str(name)] = fs.reindex(idx).astype(float)
        df = pd.DataFrame(cols)
        df["win"] = (lab.reindex(idx) > 0).astype(int)
        # 仅在粗信号触发的事件上训练（|signal|>0），标签 NaN 的行剔除
        df = df[(df["signal"].abs() > 0) & df["signal"].notna()]
        df = df.replace([np.inf, -np.inf], np.nan).dropna(subset=["win"])
        return df.reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def meta_label_samples_from_signal_log(
    min_samples: int = 200,
    lookback_days: int = 90,
    feature_freq: float = 0.2,
) -> pd.DataFrame:
    """[2026-08-13 P1-6] 从 scalp_signal_log 的 features_json 快照构造 meta 训练样本。

    接入 scalp_meta_trainer 现有采集链路：信号发生时的因子快照（breakdown +
    订单流字段）已存 features_json，事后由结算任务回填 win 标签。这里把
    「粗信号触发事件 + 真实 win」对齐成二分类 DataFrame，供
    scalp_meta_trainer.train_and_validate 训练「该不该交易」模型。

    Parameters:
        min_samples: 有效样本下限（低于此值返回空，调用方应继续采集）。
        lookback_days: 取近 N 天样本。
        feature_freq: 快照键入列门槛（在样本中出现频率 ≥ 此值才作为特征）。

    Returns:
        DataFrame：列 = [signal, dir_sign, <数值快照特征...>, win]。
    """
    import json as _json
    from sqlalchemy import text as _text
    from backend.database.connection import SessionLocal

    db = SessionLocal()
    try:
        rows = db.execute(_text(
            "SELECT factor_score, direction, win, features_json FROM scalp_signal_log "
            "WHERE settled = true AND win IS NOT NULL "
            "AND created_at >= NOW() - INTERVAL '" + str(int(lookback_days)) + " days'"
        )).fetchall()
    finally:
        db.close()

    recs = []
    for fs, direction, win, feats_raw in rows:
        try:
            feats = _json.loads(feats_raw) if feats_raw else {}
        except Exception:
            feats = {}
        if not isinstance(feats, dict):
            feats = {}
        recs.append({
            "signal": float(fs or 0),
            "dir_sign": 1.0 if str(direction) == "long" else (-1.0 if str(direction) == "short" else 0.0),
            "win": 1 if bool(win) else 0,
            "feats": feats,
        })
    if len(recs) < min_samples:
        return pd.DataFrame()

    # 快照键频率筛选（只保留数值型、出现频率达标的键）
    key_count: dict = {}
    for r in recs:
        for k, v in r["feats"].items():
            try:
                float(v)
                key_count[k] = key_count.get(k, 0) + 1
            except (TypeError, ValueError):
                continue
    n = len(recs)
    snap_cols = sorted(k for k, c in key_count.items() if c / n >= feature_freq)
    cols = {"signal": [], "dir_sign": [], "win": []}
    for c in snap_cols:
        cols[c] = []
    for r in recs:
        cols["signal"].append(r["signal"])
        cols["dir_sign"].append(r["dir_sign"])
        cols["win"].append(r["win"])
        for c in snap_cols:
            try:
                v = float(r["feats"].get(c))
                cols[c].append(v if np.isfinite(v) else 0.0)
            except (TypeError, ValueError):
                cols[c].append(0.0)
    df = pd.DataFrame(cols)
    return df[(df["signal"].abs() > 0)]


def net_ic(ic_mean: float, turnover: float, cost_per_turn: float = 0.001) -> float:
    """净 IC = 毛 IC − 换手 × 单次往返成本（默认 taker 0.0005 × 2）。"""
    return float(ic_mean) - float(turnover) * float(cost_per_turn)


def turnover(series: pd.Series) -> float:
    """截面时序换手：mean(|z_t − z_{t−1}|) / 2，取值 [0,1]。"""
    if series is None or len(series) < 2:
        return 0.0
    try:
        s = series.astype(float).dropna()
        if len(s) < 2:
            return 0.0
        return float((s.diff().abs().mean()) / 2.0)
    except Exception:
        return 0.0


def capacity_usd(
    symbol_volume_24h_usd: float,
    turnover: float,
    target_impact_pct: float = 0.0005,
) -> float:
    """多空组合容量 = 24h 成交额 × min(2%, 冲击预算/换手)。"""
    if symbol_volume_24h_usd <= 0 or turnover <= 0:
        return 0.0
    ratio = target_impact_pct / turnover
    return float(symbol_volume_24h_usd) * min(0.02, ratio)


def compute_quality_metrics(
    ic_mean: float,
    icir: float,
    factor_series: Optional[pd.Series] = None,
    volume_24h_usd: float = 0.0,
) -> FactorQualityMetrics:
    """汇总因子质量指标（供 M2 评估流水线复用）。"""
    t = turnover(factor_series) if factor_series is not None else 0.0
    return FactorQualityMetrics(
        ic_mean=float(ic_mean),
        icir=float(icir),
        net_ic=net_ic(float(ic_mean), t),
        turnover=t,
        capacity_usd=capacity_usd(volume_24h_usd, t),
    )
