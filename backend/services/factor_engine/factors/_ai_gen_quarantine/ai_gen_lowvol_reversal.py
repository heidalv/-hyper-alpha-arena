"""AI因子: 低波动反转因子 | 置信:65% | 识别市场在低波动环境下价格小幅突破后反转的潜在亏损模式。计算过去N周期ATR与近期价格区间宽度比值，若比值低于阈值且收盘价靠近区间边缘后反向移动，则信号偏向负向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Low Volatility Reversal(BaseFactor):
    """识别市场在低波动环境下价格小幅突破后反转的潜在亏损模式。计算过去N周期ATR与近期价格区间宽度比值，若比值低于阈值且收盘价靠近区间边缘后反向移动，则信号偏向负向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_lowvol_reversal",
            name="Low Volatility Reversal",
            display_name="低波动反转因子",
            description="识别市场在低波动环境下价格小幅突破后反转的潜在亏损模式。计算过去N周期ATR与近期价格区间宽度比值，若比值低于阈值且收盘价靠近区间边缘后反向移动，则信号偏向负向。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            # 参数设置
            n = 20
            atr = (data['high'] - data['low']).rolling(n).mean()  # 简化ATR
            price_range = data['high'].rolling(n).max() - data['low'].rolling(n).min()
            vol_ratio = atr / price_range  # 低波动时比值小
            # 价格在区间位置
            pos = (data['close'] - data['low'].rolling(n).min()) / (price_range + 1e-10)
            # 反转信号：靠近区间边缘后收盘方向反转
            rev_signal = ((pos > 0.8) | (pos < 0.2)) & (data['close'].diff().shift(1) * data['close'].diff() < 0)
            # 低波动下反转信号强化
            low_vol = vol_ratio < vol_ratio.quantile(0.2)
            result = -1 * rev_signal.astype(int) * low_vol.astype(int)
            return result.fillna(0).clip(-1, 1)
