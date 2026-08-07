"""AI因子: 日内买卖压力不平衡因子 | 置信:60% | 通过日内的最高、最低、收盘价位置来估计买卖力量的失衡程度。在市场regime unknown时，经常出现日内波动大但收盘位置不明的情况。计算（收盘价 - 日内最低价） / （日内最高价 - 日内最低价）的5日均值，再减去0.5得到偏离度。当该值接近0.5时，表明多空均衡（regime未知）；当偏离过大时，趋势可能延续。但此处我们反其道而行，当偏离度绝对值小于0.1时，认为市场平衡偏弱，建议避免做多。因子值负向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Intraday_Pressure_Imbalance(BaseFactor):
    """通过日内的最高、最低、收盘价位置来估计买卖力量的失衡程度。在市场regime unknown时，经常出现日内波动大但收盘位置不明的情况。计算（收盘价 - 日内最低价） / （日内最高价 - 日内最低价）的5日均值，再减去0.5得到偏离度。当该值接近0.5时，表明多空均衡（regime未知）；当偏离过大时，趋势可能延续。但此处我们反其道而行，当偏离度绝对值小于0.1时，认为市场平衡偏弱，建议避免做多。因子值负向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_intraday_imbalance",
            name="Intraday Pressure Imbalance",
            display_name="日内买卖压力不平衡因子",
            description="通过日内的最高、最低、收盘价位置来估计买卖力量的失衡程度。在市场regime unknown时，经常出现日内波动大但收盘位置不明的情况。计算（收盘价 - 日内最低价） / （日内最高价 - 日内最低价）的5日均值，再减去0.5得到偏离度。当该值接近0.5时，表明多空均衡（regime未知）；当偏离过大时，趋势可能延续。但此处我们反其道而行，当偏离度绝对值小于0.1时，认为市场平衡偏弱，建议避免做多。因子值负向。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # 日内位置
        pos = (close - low) / (high - low + 1e-10)
        avg_pos = pos.rolling(5).mean()
        deviation = avg_pos - 0.5
        # 映射到[-1,1]，当偏差绝对值小于0.1时取负值，大于0.3时取正值
        factor = np.sign(deviation) * (np.abs(deviation) * 3).clip(0, 1)
        # 但我们要识别平衡状态（regime unknown），故反转：平衡时给负值
        factor = -factor
        return factor.fillna(0)
