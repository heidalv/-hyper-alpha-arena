"""AI因子: 布林带位置与带宽收缩 | 置信:60% | 价格位于布林带中轨下方且带宽处于近期低位时，市场无趋势且偏弱，做多易亏损。因子值：计算价格相对布林带位置（0-1之间）与带宽收缩程度的乘积，再映射到[-1,1]，低值表示不利做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Bollinger_Band_Position_Squeeze(BaseFactor):
    """价格位于布林带中轨下方且带宽处于近期低位时，市场无趋势且偏弱，做多易亏损。因子值：计算价格相对布林带位置（0-1之间）与带宽收缩程度的乘积，再映射到[-1,1]，低值表示不利做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bb_zone",
            name="Bollinger Band Position & Squeeze",
            display_name="布林带位置与带宽收缩",
            description="价格位于布林带中轨下方且带宽处于近期低位时，市场无趋势且偏弱，做多易亏损。因子值：计算价格相对布林带位置（0-1之间）与带宽收缩程度的乘积，再映射到[-1,1]，低值表示不利做多。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        # 布林带参数
        period = 20
        n_std = 2
        sma = close.rolling(period).mean()
        std = close.rolling(period).std()
        upper = sma + n_std * std
        lower = sma - n_std * std
        # 价格在布林带中的位置：0=下轨，1=上轨
        pos = (close - lower) / (upper - lower + 1e-8)
        # 带宽 = (upper - lower) / sma
        bandwidth = (upper - lower) / sma
        # 带宽收缩: 当前带宽除以过去60日平均带宽，越小越压缩
        bw_ratio = bandwidth / bandwidth.rolling(60).mean()
        # 组合: 当位置<0.5且带宽收缩时因子为负
        raw = (pos - 0.5) * (1 - bw_ratio)  # 范围大致[-0.5,0.5]
        result = np.tanh(raw * 4)  # 放大到[-1,1]左右
        result = result.fillna(0)
        return result
