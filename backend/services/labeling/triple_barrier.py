"""
Triple-Barrier 标签法（P1.4，López de Prado AFML Ch.3）。

目标（方案 §4 表格）：用经济意义标签替代固定 horizon 收益。
    对每个 bar，设上轨（止盈）、下轨（止损）、垂直（时间到期）三道屏障，
    标签 = 先触碰的屏障：+1（上轨）/ -1（下轨）/ 0（时间到期未触发）。

屏障按波动率自适应缩放（避免用固定百分比硬套不同波动品种）。

这与现有"forward return 标签"的关键区别：
    - forward return 只看 N 期后涨跌，忽略路径（可能中途触发止损又被拉回）。
    - triple-barrier 反映真实交易结果（TP/SL/时间退出）。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

import numpy as np
import pandas as pd


class BarrierLabel(IntEnum):
    UPPER = 1       # 先触碰上轨（止盈）
    LOWER = -1      # 先触碰下轨（止损）
    VERTICAL = 0    # 时间到期未触发


@dataclass(frozen=True)
class TripleBarrierConfig:
    """三屏障配置。"""
    upper_mult: float = 2.0     # 上轨 = +mult × 每日波动率
    lower_mult: float = 2.0     # 下轨 = -mult × 每日波动率
    num_days: int = 5           # 垂直屏障（持有天数）
    vol_lookback: int = 20      # 每日波动率 EMA 窗口
    min_vol: float = 1e-6       # 波动率下限（防除零/过窄屏障）


def daily_volatility(prices: pd.Series, lookback: int = 20) -> pd.Series:
    """每日收益的滚动标准差（指数加权），用于自适应屏障宽度。"""
    ret = prices.pct_change()
    return ret.ewm(span=lookback, min_periods=max(2, lookback // 2)).std()


def apply_triple_barrier(
    prices: pd.Series,
    config: TripleBarrierConfig | None = None,
    *,
    events_index: pd.Index | None = None,
) -> pd.DataFrame:
    """
    对价格序列应用三屏障标签。

    参数：
        prices: 收盘价序列（pd.Series，index 为时间）。
        config: 屏障配置。
        events_index: 仅对这些时点打标签（None = 全部）。用于 meta-labeling 子集。
    返回：
        DataFrame: columns=['label', 'touch_price', 'touch_idx', 'horizon']
            - label: +1/-1/0
            - touch_price: 触碰时的价格
            - touch_idx: 触碰时点（prices 的 index 值）
            - horizon: 实际持仓 bar 数
    """
    cfg = config or TripleBarrierConfig()
    prices = prices.sort_index()
    vol = daily_volatility(prices, cfg.vol_lookback).clip(lower=cfg.min_vol)

    idxs = events_index if events_index is not None else prices.index
    n = len(prices)
    pos = {t: i for i, t in enumerate(prices.index)}

    records = []  # (index_t, row_dict)
    for t in idxs:
        i = pos.get(t)
        if i is None or i >= n - 1:
            continue
        entry = prices.iloc[i]
        v = vol.iloc[i]
        if not np.isfinite(v) or v < cfg.min_vol:
            continue
        upper = entry * (1 + cfg.upper_mult * v)
        lower = entry * (1 - cfg.lower_mult * v)
        end = min(i + cfg.num_days, n - 1)

        label = BarrierLabel.VERTICAL
        touch_i = end
        for j in range(i + 1, end + 1):
            hi = prices.iloc[j]
            lo = prices.iloc[j]
            if hi >= upper:
                label = BarrierLabel.UPPER
                touch_i = j
                break
            if lo <= lower:
                label = BarrierLabel.LOWER
                touch_i = j
                break

        records.append((t, {
            "label": int(label),
            "touch_price": float(prices.iloc[touch_i]),
            "touch_idx": prices.index[touch_i],
            "horizon": touch_i - i,
        }))

    if not records:
        return pd.DataFrame(columns=["label", "touch_price", "touch_idx", "horizon"])
    index = pd.Index([t for t, _ in records], name=prices.index.name)
    df = pd.DataFrame([r for _, r in records], index=index)
    return df
