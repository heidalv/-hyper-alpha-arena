"""AI因子: 波动率区间因子 | 置信:60% | 基于当前价格在近期最高最低区间内的位置与近期ATR比例，识别高波动率趋势区（+1）和低波动率随机区（-1）。当价格靠近区间边缘且ATR较大时倾向趋势延续；当价格处于中间且ATR较小时倾向震荡或未知状态。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Zone_Indicator(BaseFactor):
    """基于当前价格在近期最高最低区间内的位置与近期ATR比例，识别高波动率趋势区（+1）和低波动率随机区（-1）。当价格靠近区间边缘且ATR较大时倾向趋势延续；当价格处于中间且ATR较小时倾向震荡或未知状态。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_zone",
            name="Volatility Zone Indicator",
            display_name="波动率区间因子",
            description="基于当前价格在近期最高最低区间内的位置与近期ATR比例，识别高波动率趋势区（+1）和低波动率随机区（-1）。当价格靠近区间边缘且ATR较大时倾向趋势延续；当价格处于中间且ATR较小时倾向震荡或未知状态。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        period = 20
        high = data['high']
        low = data['low']
        close = data['close']
        # 计算最高价和最低价
        rolling_high = high.rolling(period).max()
        rolling_low = low.rolling(period).min()
        range_val = rolling_high - rolling_low
        # 价格在区间内的相对位置 (0~1)
        pos = (close - rolling_low) / range_val.replace(0, np.nan)
        # ATR
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr = tr.rolling(period).mean()
        # 波动率比例：ATR相对于区间宽度
        vol_ratio = atr / range_val.replace(0, np.nan)
        # 综合得分：当位置靠近极端（<0.2或>0.8）且vol_ratio较大时，趋势信号强；否则震荡信号
        score = np.where(pos < 0.2, -1.0 * (1 - pos/0.2), 0.0)
        score = np.where(pos > 0.8, 1.0 * ((pos-0.8)/0.2), score)
        # 用vol_ratio加强信号：高波动率时信号更强
        vol_factor = vol_ratio.fillna(0.5) * 2.0 - 1.0  # 映射到[-1,1]
        result = score * (0.5 + 0.5 * vol_factor.abs())
        result = result.clip(-1, 1)
        return result
