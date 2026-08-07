"""AI因子: ATR收缩突破失败因子 | 置信:55% | 价格在窄幅震荡后试图向上突破，但波动率（ATR）未同步放大，突破易失败导致止损。计算当前ATR与近期最大ATR的比率，以及价格突破布林带上轨的程度，综合输出负值表示做多风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ATR_Squeeze_Breakdown(BaseFactor):
    """价格在窄幅震荡后试图向上突破，但波动率（ATR）未同步放大，突破易失败导致止损。计算当前ATR与近期最大ATR的比率，以及价格突破布林带上轨的程度，综合输出负值表示做多风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_atr_squeeze",
            name="ATR Squeeze Breakdown",
            display_name="ATR收缩突破失败因子",
            description="价格在窄幅震荡后试图向上突破，但波动率（ATR）未同步放大，突破易失败导致止损。计算当前ATR与近期最大ATR的比率，以及价格突破布林带上轨的程度，综合输出负值表示做多风险。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        n = 14
        high = data['high'].values
        low = data['low'].values
        close = data['close'].values
        prev_close = np.roll(close, 1)
        tr = np.maximum(high - low, np.abs(high - prev_close), np.abs(low - prev_close))
        atr = pd.Series(tr).rolling(n).mean().values
        # 取最近20周期内的最大ATR
        lookback = 20
        if len(atr) < lookback:
            return pd.Series(0.0, index=data.index)
        atr_max = np.max(atr[-lookback:])
        atr_ratio = atr[-1] / atr_max if atr_max > 0 else 1.0
        # 布林带上轨：20日均线+2倍标准差
        sma20 = pd.Series(close).rolling(20).mean().values
        std20 = pd.Series(close).rolling(20).std().values
        upper_band = sma20 + 2 * std20
        # 价格突破上轨的程度
        if len(upper_band) > 0 and upper_band[-1] > 0:
            break_pct = (close[-1] - upper_band[-1]) / upper_band[-1]
        else:
            break_pct = 0
        # 组合：ATR收缩且价格突破上轨时看空
        raw = (1 - atr_ratio) * break_pct
        result = np.clip(raw * 5 - 0.2, -1, 1)
        return pd.Series(result, index=data.index[-1:], name='atr_squeeze')
