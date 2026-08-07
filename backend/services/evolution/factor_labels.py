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

import pandas as pd

FEATURE_FACTOR_LABELS_ENABLED = os.getenv("FEATURE_FACTOR_LABELS_ENABLED", "false").lower() in (
    "1", "true", "yes", "on",
)


@dataclass
class FactorLabelConfig:
    """标签参数（默认值来自设计文档 §2.1）。"""
    horizon_bars: int = 12        # 5m 因子默认 12 根（1h）
    vol_mult: float = 1.5         # 上下障碍 = 当日波动率 × vol_mult
    min_vol: float = 0.0001       # 波动率下限，过低跳过
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
    """meta-labeling 样本生成（骨架）。

    设计：在粗信号（因子方向）触发的事件集上，用 5~8 个特征
    （ATR%、量比、盘口失衡、OI delta）训练"是否值得交易"二分类。
    当前仅返回空 DataFrame，等待 FEATURE_FACTOR_LABELS_ENABLED 启用后实现。
    """
    return pd.DataFrame()


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
