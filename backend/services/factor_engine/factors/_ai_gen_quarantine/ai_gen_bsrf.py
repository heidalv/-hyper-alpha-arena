"""AI因子: 方向偏好与止损风险 | 置信:50% | 结合价格相对位置和近期止损频率，构建风险规避因子。亏损模式中多次止损（sl）和止盈（tp）亏损（概率低但依然亏损），表明市场无序波动。该因子通过计算价格在布林带中的位置以及近N根K线内价格突破区间上下界的次数，识别容易触发止损的高风险时段，输出负值建议避免做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Bias_and_Stop_Loss_Risk_Factor(BaseFactor):
    """结合价格相对位置和近期止损频率，构建风险规避因子。亏损模式中多次止损（sl）和止盈（tp）亏损（概率低但依然亏损），表明市场无序波动。该因子通过计算价格在布林带中的位置以及近N根K线内价格突破区间上下界的次数，识别容易触发止损的高风险时段，输出负值建议避免做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bsrf",
            name="Bias and Stop Loss Risk Factor",
            display_name="方向偏好与止损风险",
            description="结合价格相对位置和近期止损频率，构建风险规避因子。亏损模式中多次止损（sl）和止盈（tp）亏损（概率低但依然亏损），表明市场无序波动。该因子通过计算价格在布林带中的位置以及近N根K线内价格突破区间上下界的次数，识别容易触发止损的高风险时段，输出负值建议避免做多。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 布林带位置
        window = 20
        mean = data['close'].rolling(window).mean()
        std = data['close'].rolling(window).std()
        zscore = (data['close'] - mean) / (std + 1e-8)
        # 近10个周期内价格超出前一个布林带的次数
        upper = mean + 2 * std
        lower = mean - 2 * std
        exceed_upper = (data['high'].shift(1) > upper.shift(1)).rolling(10).sum()
        exceed_lower = (data['low'].shift(1) < lower.shift(1)).rolling(10).sum()
        # 止损风险评分：价格靠近上轨且近期突破次数多，则容易回调触发止损
        risk_up = np.clip(zscore, 0, 1) * (exceed_upper / 10)
        risk_down = np.clip(-zscore, 0, 1) * (exceed_lower / 10)
        # 做多亏损模式下，输出负值表示不建议做多，即风险较高时因子为负
        factor = -risk_up  # 只考虑上轨风险，因为做多易亏损
        return factor.fillna(0)
