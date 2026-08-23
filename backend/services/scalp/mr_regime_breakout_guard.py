"""MR Regime Breakout Guard — regime 突变实时守卫（2026-08-23）。

背景：震荡均值回归（MR）打法只在 regime==ranging 且 48×5m 振幅落在
[1.5%,5%] 时开单，但 regime 分类是低频滞后信号。趋势转换日（如 8/19、
8/21）MR 仍按震荡"接飞刀"，单日亏 -19 / -36。

本守卫在 MR 分流【之前】用两条高频证据判断"震荡→趋势"的实时突变：

1. 波动率爆发：最近 24 根 5m（2h）收盘价 pct_change 的 std（realized
   vol）与再之前 24 根的 std 比较，比值 > MR_BREAKOUT_VOL_RATIO（默认
   2.5）→ 判定趋势转换前兆。
2. ADX 突变：14 期 Wilder ADX，当前值与其 24 根前（约 2h 前）比较，
   增长 > MR_BREAKOUT_ADX_RATIO（默认 1.8 倍）且当前 ADX >
   MR_BREAKOUT_ADX_MIN（默认 25）→ 判定趋势启动。

任一命中即返回 (True, reason)；守卫只负责"拦截 MR、回落到趋势打法"，绝不
抛异常阻断主流程：任何异常（列缺失 / K 线不足 / 计算失败）都 fail-open 返回
(False, "") 并 debug 日志。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 默认阈值（env 缺失时使用；env 名与任务规格一致）
_DEFAULT_VOL_RATIO = 2.5
_DEFAULT_ADX_RATIO = 1.8
_DEFAULT_ADX_MIN = 25.0

# 最少 K 线数量（5m）：至少覆盖 ADX(14) 双次 Wilder 平滑 + 24 根回看比较。
_MIN_KLINES = 60


def _env_float(name: str, default: float) -> float:
    """读取 env 浮点配置，缺失/非法时回退默认（fail-open，绝不抛异常）。"""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.debug("[MRBreakoutGuard] env %s=%r 非法，回退默认 %.2f", name, raw, default)
        return default


def _wilder_rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder 平滑（RMA），等价于 EMA(alpha=1/period, adjust=False)。"""
    return series.astype(float).ewm(
        alpha=1.0 / period, adjust=False, min_periods=period,
    ).mean()


def _wilder_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """14 期 Wilder ADX（+DM/-DM/TR 平滑 → +DI/-DI → DX → ADX 类平滑）。

    返回与 df 等长、NaN 填充前导的 ADX Series；index 与 df 对齐。
    """
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    # True Range
    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # 方向运动（Wilder 定义：+DM/-DM 二选一，且必须为正）
    up_move = high - prev_high
    down_move = prev_low - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=df.index)
    minus_dm = pd.Series(minus_dm, index=df.index)

    atr = _wilder_rma(tr, period)
    plus_di = 100.0 * _wilder_rma(plus_dm, period) / atr.replace(0.0, np.nan)
    minus_di = 100.0 * _wilder_rma(minus_dm, period) / atr.replace(0.0, np.nan)

    denom = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / denom
    dx = dx.fillna(0.0)  # +DI+-DI==0（纯无方向）时 DX 记 0

    return _wilder_rma(dx, period)


def mr_breakout_danger(market_data: Dict[str, Any]) -> Tuple[bool, str]:
    """判定当前是否处于 regime 突变（震荡→趋势）危险区。

    返回 (危险?, 原因)。任何异常一律 fail-open 返回 (False, "")，绝不抛异常。
    """
    try:
        df = market_data.get("klines") if isinstance(market_data, dict) else None
        if df is None:
            logger.debug("[MRBreakoutGuard] 无 klines，fail-open 放行")
            return False, ""
        if not isinstance(df, pd.DataFrame):
            df = pd.DataFrame(df)
        if len(df) < _MIN_KLINES:
            logger.debug("[MRBreakoutGuard] K线不足 %d 根（实际 %d），fail-open 放行",
                         _MIN_KLINES, len(df))
            return False, ""
        need_cols = ("open", "high", "low", "close")
        missing = [c for c in need_cols if c not in df.columns]
        if missing:
            logger.debug("[MRBreakoutGuard] 缺失列 %s，fail-open 放行", missing)
            return False, ""

        vol_ratio_th = _env_float("MR_BREAKOUT_VOL_RATIO", _DEFAULT_VOL_RATIO)
        adx_ratio_th = _env_float("MR_BREAKOUT_ADX_RATIO", _DEFAULT_ADX_RATIO)
        adx_min_th = _env_float("MR_BREAKOUT_ADX_MIN", _DEFAULT_ADX_MIN)

        close = df["close"].astype(float)

        # ── 条件 1：realized vol 爆发（最近 24 根 vs 再之前 24 根）──
        recent_std = float(close.tail(24).pct_change().dropna().std())
        prior_std = float(close.iloc[-48:-24].pct_change().dropna().std())
        if not np.isfinite(recent_std):
            recent_std = 0.0
        if not np.isfinite(prior_std) or prior_std <= 0.0:
            vol_ratio = float("inf") if recent_std > 0.0 else 1.0
        else:
            vol_ratio = recent_std / prior_std
        if vol_ratio > vol_ratio_th:
            return True, (
                f"波动率爆发: 近2h realized_vol={recent_std:.6f} "
                f"为前2h的{vol_ratio:.2f}倍(阈值{vol_ratio_th:.2f})"
            )

        # ── 条件 2：ADX 突变（当前 vs 24 根前，增长且绝对水平足够）──
        adx = _wilder_adx(df, period=14)
        if len(adx) >= 25:
            cur_adx = float(adx.iloc[-1])
            prev_adx = float(adx.iloc[-25])
            if np.isfinite(cur_adx) and np.isfinite(prev_adx) and prev_adx > 0.0:
                adx_growth = cur_adx / prev_adx
                if adx_growth > adx_ratio_th and cur_adx > adx_min_th:
                    return True, (
                        f"ADX突变: 当前ADX={cur_adx:.2f} 较24根前({prev_adx:.2f})"
                        f"增长{adx_growth:.2f}倍(阈值{adx_ratio_th:.2f},下限{adx_min_th:.2f})"
                    )

        return False, ""
    except Exception as _e:  # noqa: BLE001 — fail-open 铁律：绝不让守卫抛异常
        logger.debug("[MRBreakoutGuard] 计算失败，fail-open 放行: %r", _e)
        return False, ""
