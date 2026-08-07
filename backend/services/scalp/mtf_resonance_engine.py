"""
MTF三重屏幕共振引擎 — compute_mtf_resonance（P1，规划文档§5.5）。

背景（已核实）：现有 `ScalpMtfConstraint`（scalp_mtf_constraint.py）只做"是否
允许交易"的约束检查，依赖 OrchBG 后台预算好的粗粒度 `mid_bias`/`short_bias`
字符串（只有4h+15m两层，且是离散的bullish/bearish/neutral，没有强度）。规划
文档§5.5要求升级为三层加权共振评分模型：

| 层级 | 时间框架 | 角色 | 基础权重 |
|---|---|---|---|
| 情境层 | 1H | 趋势方向确认(EMA200/ADX) | 50% |
| 确认层 | 15M | 动量一致性(MACD/RSI) | 30% |
| 执行层 | 5M | 精确触发(CVD背离/OFI极端) | 20% |

ATR自适应：5m/1h ATR噪声比>3.0时降低5m权重，释放权重按0.5/0.5分配给15m/1h。
共振决策矩阵：三层同向=全仓(1.0)；1H与5M分歧过大(方向相反)=不交易。

技术指标计算复用 backtest_engine/pipeline_replay.py 的 calc_ema/calc_rsi/calc_macd
（"与实盘完全一致"的纯函数，本模块不重新发明一套可能口径不一致的指标计算）。
执行层直接复用 P1 新增的 CVDDivergenceFactor（orderflow_crypto_factors.py），
而不是重新写一套CVD/OFI逻辑——保持"一个信号只有一份计算逻辑"。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

BASE_WEIGHT_1H = 0.5
BASE_WEIGHT_15M = 0.3
BASE_WEIGHT_5M = 0.2
ATR_NOISE_RATIO_THRESHOLD = 3.0
BIAS_MAGNITUDE_THRESHOLD = 0.15  # |score| 低于此值判定为 neutral

_CACHE_TTL_SEC = 240  # 1h/15m数据变化慢，缓存4分钟减少scalp高频循环下的DB查询压力
_kline_cache: Dict[str, Tuple[float, pd.DataFrame]] = {}


def _cfg(name: str, default: Any) -> Any:
    from backend.config import settings as _s
    return getattr(_s, name, default)


@dataclass
class LayerScore:
    timeframe: str
    bias: str = "neutral"          # bullish / bearish / neutral
    score: float = 0.0             # [-1, 1]，符号=方向，绝对值=强度
    detail: str = ""


@dataclass
class MTFResonanceResult:
    available: bool = False
    resonance_score: float = 0.0
    context_layer: Optional[LayerScore] = None    # 1H
    confirm_layer: Optional[LayerScore] = None    # 15M
    execution_layer: Optional[LayerScore] = None  # 5M
    weights_used: Dict[str, float] = field(default_factory=dict)
    no_trade: bool = False
    size_multiplier: float = 1.0
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "available": self.available, "resonance_score": round(self.resonance_score, 3),
            "context_1h": vars(self.context_layer) if self.context_layer else None,
            "confirm_15m": vars(self.confirm_layer) if self.confirm_layer else None,
            "execution_5m": vars(self.execution_layer) if self.execution_layer else None,
            "weights_used": self.weights_used, "no_trade": self.no_trade,
            "size_multiplier": round(self.size_multiplier, 3), "reason": self.reason,
        }


def _get_cached_klines(symbol: str, period: str, count: int) -> Optional[pd.DataFrame]:
    key = f"{symbol}_{period}_{count}"
    now = time.time()
    cached = _kline_cache.get(key)
    if cached and (now - cached[0]) < _CACHE_TTL_SEC:
        return cached[1]
    try:
        from backend.services.kline_data_service import kline_service
        # 决策同源：不硬编码交易所，走 get_klines_from_db → data_center(purpose=trade)
        raw = kline_service.get_klines_from_db(symbol.upper(), period, count)
        if not raw or len(raw) < 20:
            return None
        df = pd.DataFrame(raw)
        _kline_cache[key] = (now, df)
        return df
    except Exception as e:
        logger.debug(f"[MTFResonance] {symbol}@{period} 取数失败: {e}")
        return None


def _ema(series: np.ndarray, span: int) -> np.ndarray:
    from backend.services.backtest_engine.pipeline_replay import calc_ema
    return calc_ema(series, span)


def _rsi(series: np.ndarray, period: int = 14) -> np.ndarray:
    from backend.services.backtest_engine.pipeline_replay import calc_rsi
    return calc_rsi(series, period)


def _macd(series: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    from backend.services.backtest_engine.pipeline_replay import calc_macd
    return calc_macd(series)


def _true_range(df: pd.DataFrame) -> np.ndarray:
    high = df["high"].astype(float).values
    low = df["low"].astype(float).values
    close = df["close"].astype(float).values
    return np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
    )


def _atr_pct(df: pd.DataFrame, period: int = 14) -> float:
    """ATR占最新收盘价的比例，用于5m/1h噪声比的ATR自适应权重。"""
    try:
        close = df["close"].astype(float).values
        if len(close) < period + 1:
            return 0.0
        tr = _true_range(df)
        atr = float(np.mean(tr[-period:]))
        return atr / close[-1] if close[-1] else 0.0
    except Exception:
        return 0.0


def _simple_adx(df: pd.DataFrame, period: int = 14) -> float:
    """Wilder ADX 独立实现（不依赖 FactorEngine 实例方法，保持本模块无状态可复用）。"""
    try:
        high = df["high"].astype(float).values
        low = df["low"].astype(float).values
        if len(high) < period * 2:
            return 20.0
        tr = _true_range(df)
        up_move = high[1:] - high[:-1]
        down_move = low[:-1] - low[1:]
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        atr = pd.Series(tr).ewm(alpha=1 / period, adjust=False).mean().values
        plus_di = 100 * pd.Series(plus_dm).ewm(alpha=1 / period, adjust=False).mean().values / (atr + 1e-10)
        minus_di = 100 * pd.Series(minus_dm).ewm(alpha=1 / period, adjust=False).mean().values / (atr + 1e-10)
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = float(np.mean(dx[-period:]))
        return adx if np.isfinite(adx) else 20.0
    except Exception:
        return 20.0


def _context_layer_1h(df: pd.DataFrame) -> LayerScore:
    """情境层(1H)：EMA200趋势方向 + ADX强度。"""
    close = df["close"].astype(float).values
    if len(close) < 30:
        return LayerScore("1h", detail="数据不足")
    ema_span = min(200, max(20, len(close) - 1))
    ema = _ema(close, ema_span)
    adx = _simple_adx(df)
    trend_up = close[-1] > ema[-1]
    dist_pct = abs(close[-1] - ema[-1]) / ema[-1] if ema[-1] else 0.0
    adx_strength = min(1.0, adx / 40.0)
    magnitude = min(1.0, adx_strength * 0.7 + min(dist_pct * 10, 1.0) * 0.3)
    score = magnitude if trend_up else -magnitude
    bias = (
        "bullish" if trend_up and magnitude > BIAS_MAGNITUDE_THRESHOLD else
        ("bearish" if not trend_up and magnitude > BIAS_MAGNITUDE_THRESHOLD else "neutral")
    )
    return LayerScore("1h", bias=bias, score=round(float(score), 3),
                       detail=f"EMA{ema_span}={ema[-1]:.4f} close={close[-1]:.4f} ADX={adx:.1f}")


def _confirm_layer_15m(df: pd.DataFrame) -> LayerScore:
    """确认层(15M)：MACD柱方向 + RSI动量一致性。"""
    close = df["close"].astype(float).values
    if len(close) < 30:
        return LayerScore("15m", detail="数据不足")
    macd_line, signal_line = _macd(close)
    rsi = _rsi(close)
    macd_hist = float(macd_line[-1] - signal_line[-1])
    rsi_val = float(rsi[-1])
    macd_sign = 1.0 if macd_hist > 0 else (-1.0 if macd_hist < 0 else 0.0)
    macd_norm = min(1.0, abs(macd_hist) / (abs(close[-1]) * 0.002 + 1e-10))
    rsi_bias = np.clip((rsi_val - 50.0) / 50.0, -1, 1)
    score = float(np.clip(0.5 * macd_sign * macd_norm + 0.5 * rsi_bias, -1, 1))
    bias = "bullish" if score > BIAS_MAGNITUDE_THRESHOLD else (
        "bearish" if score < -BIAS_MAGNITUDE_THRESHOLD else "neutral")
    return LayerScore("15m", bias=bias, score=round(score, 3),
                       detail=f"MACD_hist={macd_hist:.6f} RSI={rsi_val:.1f}")


def _execution_layer_5m(df: pd.DataFrame, symbol: str) -> LayerScore:
    """执行层(5M)：CVD背离(复用P1新增因子，见orderflow_crypto_factors.py)。"""
    cvd_val = 0.0
    try:
        from backend.services.factor_engine.factors.derivatives.orderflow_crypto_factors import (
            CVDDivergenceFactor,
        )
        cvd_series = CVDDivergenceFactor({}).calculate(df)
        if cvd_series is not None and len(cvd_series) > 0:
            v = float(cvd_series.iloc[-1])
            if np.isfinite(v):
                cvd_val = v
    except Exception as e:
        logger.debug(f"[MTFResonance] {symbol} CVD背离计算失败(降级为0): {e}")

    score = float(np.clip(cvd_val / 3.0, -1, 1))  # z-score量级/3做粗归一化到[-1,1]
    bias = "bullish" if score > BIAS_MAGNITUDE_THRESHOLD else (
        "bearish" if score < -BIAS_MAGNITUDE_THRESHOLD else "neutral")
    return LayerScore("5m", bias=bias, score=round(score, 3), detail=f"cvd_divergence_z={cvd_val:.3f}")


def compute_mtf_resonance(
    symbol: str,
    scalp_direction: str,
    market_data: Optional[Dict[str, Any]] = None,
) -> MTFResonanceResult:
    """三重屏幕共振评分主入口。任何环节失败都安全降级为 available=False，不阻断上层调用方。"""
    if not bool(_cfg("SCALP_MTF_RESONANCE_ENABLED", True)):
        return MTFResonanceResult(reason="resonance_disabled")

    try:
        df_5m = None
        if isinstance(market_data, dict):
            _k = market_data.get("klines")
            if _k is not None and hasattr(_k, "__len__") and len(_k) >= 20:
                df_5m = _k if isinstance(_k, pd.DataFrame) else pd.DataFrame(_k)
        if df_5m is None:
            df_5m = _get_cached_klines(symbol, "5m", 60)

        df_15m = None
        if isinstance(market_data, dict):
            _k15 = market_data.get("klines_15m")
            if _k15 is not None and hasattr(_k15, "__len__") and len(_k15) >= 20:
                df_15m = _k15 if isinstance(_k15, pd.DataFrame) else pd.DataFrame(_k15)
        if df_15m is None:
            df_15m = _get_cached_klines(symbol, "15m", 60)

        df_1h = _get_cached_klines(symbol, "1h", 250)

        if df_1h is None or df_15m is None or df_5m is None:
            return MTFResonanceResult(reason="多周期数据不足，跳过共振评分")

        context = _context_layer_1h(df_1h)
        confirm = _confirm_layer_15m(df_15m)
        execution = _execution_layer_5m(df_5m, symbol)

        atr_5m = _atr_pct(df_5m)
        atr_1h = _atr_pct(df_1h)
        noise_ratio = (atr_5m / atr_1h) if atr_1h > 1e-10 else 0.0
        w1h, w15m, w5m = BASE_WEIGHT_1H, BASE_WEIGHT_15M, BASE_WEIGHT_5M
        if noise_ratio > ATR_NOISE_RATIO_THRESHOLD:
            released = w5m
            w5m = 0.0
            w1h += released * 0.5
            w15m += released * 0.5

        resonance_score = context.score * w1h + confirm.score * w15m + execution.score * w5m

        biases = [context.bias, confirm.bias, execution.bias]
        reason_parts = [
            f"1h={context.bias}({context.score:+.2f}) 15m={confirm.bias}({confirm.score:+.2f}) "
            f"5m={execution.bias}({execution.score:+.2f}) noise_ratio={noise_ratio:.2f}"
        ]

        no_trade = (
            context.bias != "neutral" and execution.bias != "neutral" and context.bias != execution.bias
        )
        if no_trade:
            reason_parts.append("1H与5M方向分歧过大→不交易")

        if all(b == biases[0] and b != "neutral" for b in biases):
            size_mult = 1.0
            reason_parts.append("三层同向→全仓")
        elif no_trade:
            size_mult = 0.0
        else:
            size_mult = float(np.clip(0.5 + abs(resonance_score) * 0.5, 0.3, 1.0))

        scalp_bias = (
            "bullish" if (scalp_direction or "").lower() in ("long", "buy", "bullish") else
            ("bearish" if (scalp_direction or "").lower() in ("short", "sell", "bearish") else "neutral")
        )
        if scalp_bias != "neutral" and resonance_score != 0 and not no_trade:
            resonance_bias = "bullish" if resonance_score > 0 else "bearish"
            if resonance_bias != scalp_bias and abs(resonance_score) > 0.2:
                size_mult *= 0.6
                reason_parts.append(f"共振方向({resonance_bias})与scalp意图({scalp_bias})不一致→额外缩仓×0.6")

        return MTFResonanceResult(
            available=True, resonance_score=round(float(resonance_score), 3),
            context_layer=context, confirm_layer=confirm, execution_layer=execution,
            weights_used={"1h": round(w1h, 3), "15m": round(w15m, 3), "5m": round(w5m, 3)},
            no_trade=no_trade, size_multiplier=round(max(0.0, size_mult), 3),
            reason="; ".join(reason_parts),
        )
    except Exception as e:
        logger.debug(f"[MTFResonance] {symbol} 共振评分异常(安全降级): {e}")
        return MTFResonanceResult(reason=f"计算异常: {e}")
