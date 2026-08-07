"""AI因子: 波动趋势比 | 置信:55% | 当市场波动率较高（ATR大）但趋势强度弱（ADX低）时，容易陷入震荡行情，导致止损或超时亏损。该因子通过计算ATR(20)与ADX(20)的比值，并归一化到[-1,1]，正值表示波动大而趋势弱，负值表示趋势明确。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Trend_Ratio(BaseFactor):
    """当市场波动率较高（ATR大）但趋势强度弱（ADX低）时，容易陷入震荡行情，导致止损或超时亏损。该因子通过计算ATR(20)与ADX(20)的比值，并归一化到[-1,1]，正值表示波动大而趋势弱，负值表示趋势明确。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_voltrend_ratio",
            name="Volatility Trend Ratio",
            display_name="波动趋势比",
            description="当市场波动率较高（ATR大）但趋势强度弱（ADX低）时，容易陷入震荡行情，导致止损或超时亏损。该因子通过计算ATR(20)与ADX(20)的比值，并归一化到[-1,1]，正值表示波动大而趋势弱，负值表示趋势明确。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: DataFrame with columns ['open','high','low','close','volume']
        df = data.copy()
        # 计算ATR
        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift(1)).abs()
        low_close = (df['low'] - df['close'].shift(1)).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(20).mean()
        # 计算ADX
        close = df['close']
        high = df['high']
        low = df['low']
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        tr_period = tr.rolling(14).sum()
        plus_di = 100 * pd.Series(plus_dm, index=df.index).rolling(14).sum() / tr_period
        minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(14).sum() / tr_period
        dx = ((plus_di - minus_di).abs() / (plus_di + minus_di)) * 100
        adx = dx.rolling(14).mean()
        # 计算比值，避免除以0
        adx_safe = adx.replace(0, np.nan).fillna(0.01)
        ratio = atr / (adx_safe * np.finfo(float).eps)  # 实际用 atr / adx_safe
        # 标准化到[-1,1] 使用tanh
        normalized = np.tanh((ratio - ratio.rolling(252).mean()) / (ratio.rolling(252).std() + 1e-9))
        return normalized.fillna(0.0)
