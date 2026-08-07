"""AI因子: 未知状态风险 | 置信:60% | 识别市场处于不确定状态（低波动、低成交、无趋势）的风险。通过归一化波动率、成交量变化率与趋势强度复合计算，当三者均处于中等偏低水平时输出强负值，预示做多易亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Unknown_Regime_Risk(BaseFactor):
    """识别市场处于不确定状态（低波动、低成交、无趋势）的风险。通过归一化波动率、成交量变化率与趋势强度复合计算，当三者均处于中等偏低水平时输出强负值，预示做多易亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unkn_risk",
            name="Unknown Regime Risk",
            display_name="未知状态风险",
            description="识别市场处于不确定状态（低波动、低成交、无趋势）的风险。通过归一化波动率、成交量变化率与趋势强度复合计算，当三者均处于中等偏低水平时输出强负值，预示做多易亏损。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        volume = data['volume']
        high = data['high']
        low = data['low']

        # 波动率：ATR(20)归一化
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr = tr.rolling(20).mean()
        norm_atr = atr / close * 100
        scaled_atr = 1 - 2 * (norm_atr - norm_atr.rolling(60).min()) / (norm_atr.rolling(60).max() - norm_atr.rolling(60).min() + 1e-8)

        # 成交量变化率：20日均量相对60日均量
        avg_vol_20 = volume.rolling(20).mean()
        avg_vol_60 = volume.rolling(60).mean()
        vol_ratio = avg_vol_20 / avg_vol_60
        scaled_vol = 1 - 2 * (vol_ratio - 0.5) / 1.0

        # 趋势强度：用20日RSI偏离中值
        change = close.diff()
        gain = change.where(change > 0, 0)
        loss = -change.where(change < 0, 0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / (avg_loss + 1e-8)
        rsi = 100 - 100 / (1 + rs)
        trend_strength = abs(rsi - 50) / 50  # 0~1
        scaled_trend = 1 - 2 * trend_strength

        # 复合因子：取三者的平均，然后缩放到[-1,1]
        composite = (scaled_atr + scaled_vol + scaled_trend) / 3
        result = composite.clip(-1, 1)
        return result
