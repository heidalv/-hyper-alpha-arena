"""AI因子: 趋势动量因子 | 置信:65% | 计算短期和长期EMA的差值，结合价格变化率（ROC），并考虑波动率调整。当短期均线下穿长期均线且ROC为负时，给出负向信号，反映不利做多的趋势环境。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Momentum_with_Regime_Sensitivity(BaseFactor):
    """计算短期和长期EMA的差值，结合价格变化率（ROC），并考虑波动率调整。当短期均线下穿长期均线且ROC为负时，给出负向信号，反映不利做多的趋势环境。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_mom",
            name="Trend Momentum with Regime Sensitivity",
            display_name="趋势动量因子",
            description="计算短期和长期EMA的差值，结合价格变化率（ROC），并考虑波动率调整。当短期均线下穿长期均线且ROC为负时，给出负向信号，反映不利做多的趋势环境。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 输入data: DataFrame with columns ['open','high','low','close','volume']
        close = data['close']
        # 计算短期EMA和长期EMA
        ema_short = close.ewm(span=12, adjust=False).mean()
        ema_long = close.ewm(span=26, adjust=False).mean()
        # 价格变化率 (ROC 10日)
        roc = close.pct_change(periods=10)
        # 趋势强度：短期-长期差值，标准化
        trend_diff = (ema_short - ema_long) / close
        # 结合ROC，如果趋势差为负且ROC为负，强化负信号
        combined = trend_diff * 0.5 + roc * 0.5
        # 使用tanh压缩到[-1,1]
        result = np.tanh(combined * 2)
        return result.fillna(0)
