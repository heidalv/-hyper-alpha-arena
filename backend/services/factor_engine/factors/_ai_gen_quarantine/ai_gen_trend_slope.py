"""AI因子: 趋势斜率强度因子 | 置信:65% | 基于指数移动平均斜率与ATR的比值，衡量趋势的可靠性。斜率低且价格游走于均线附近时，趋势弱易出现未知regime亏损；斜率大且方向明确时，趋势强适合做多。输出范围[-1,1]，正值表示强上升趋势。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Strength_Slope(BaseFactor):
    """基于指数移动平均斜率与ATR的比值，衡量趋势的可靠性。斜率低且价格游走于均线附近时，趋势弱易出现未知regime亏损；斜率大且方向明确时，趋势强适合做多。输出范围[-1,1]，正值表示强上升趋势。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_slope",
            name="Trend Strength Slope",
            display_name="趋势斜率强度因子",
            description="基于指数移动平均斜率与ATR的比值，衡量趋势的可靠性。斜率低且价格游走于均线附近时，趋势弱易出现未知regime亏损；斜率大且方向明确时，趋势强适合做多。输出范围[-1,1]，正值表示强上升趋势。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        ema5 = close.ewm(span=5).mean()
        ema20 = close.ewm(span=20).mean()
        slope = (ema5 - ema20) / (ema20 + 1e-10)
        # ATR14
        tr = np.maximum(high - low, np.maximum(np.abs(high - close.shift(1)), np.abs(low - close.shift(1))))
        atr14 = tr.rolling(14).mean()
        atr_ratio = slope / (atr14 / close + 1e-10)
        # 标准化到[-1,1]
        result = np.tanh(atr_ratio * 10)
        # 对趋势方向敏感，正值为上升趋势
        return result.fillna(0)
