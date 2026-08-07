"""AI因子: 趋势状态强度 | 置信:65% | 基于ADX和DI差值识别市场趋势状态。当ADX>25时视为趋势行情，输出DI+与DI-的归一化差值（范围-1到1）；当ADX<=25时视为震荡/未知状态，输出接近0的微弱信号，避免在趋势不明时交易。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendRegimeStrength(BaseFactor):
    """基于ADX和DI差值识别市场趋势状态。当ADX>25时视为趋势行情，输出DI+与DI-的归一化差值（范围-1到1）；当ADX<=25时视为震荡/未知状态，输出接近0的微弱信号，避免在趋势不明时交易。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_regime",
            name="Trend Regime Strength",
            display_name="趋势状态强度",
            description="基于ADX和DI差值识别市场趋势状态。当ADX>25时视为趋势行情，输出DI+与DI-的归一化差值（范围-1到1）；当ADX<=25时视为震荡/未知状态，输出接近0的微弱信号，避免在趋势不明时交易。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # 计算ADX
        period = 14
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        up = high - high.shift()
        down = low.shift() - low
        pos = ((up > down) & (up > 0)) * up
        neg = ((down > up) & (down > 0)) * down
        sma_pos = pos.rolling(period).sum()
        sma_neg = neg.rolling(period).sum()
        di_plus = 100 * sma_pos / (atr * period)
        di_minus = 100 * sma_neg / (atr * period)
        dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus)
        adx = dx.rolling(period).mean()
        # 方向信号
        diff = di_plus - di_minus
        norm_diff = diff / 100.0  # 归一化到-1~1
        # 当ADX低于阈值时，压缩信号接近0
        threshold = 25
        weight = np.where(adx > threshold, 1.0, adx / threshold * 0.3)  # 弱趋势下输出微弱信号
        result = norm_diff * weight
        result = result.clip(-1, 1)
        return result
