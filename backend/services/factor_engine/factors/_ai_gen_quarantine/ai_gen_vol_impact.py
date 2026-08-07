"""AI因子: 成交量冲击比 | 置信:60% | 衡量单位成交量对价格的推动效率，低冲击比可能暗示市场流动性陷阱或被动成交。计算滚动窗口内价格变化绝对值与成交量之比，并与历史均值比较。输出[-1,1]，负值表示当前冲击比低于历史均值（价格变动小但成交量大），预示反转或停滞风险；正值表示高效推动。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeImpactRatio(BaseFactor):
    """衡量单位成交量对价格的推动效率，低冲击比可能暗示市场流动性陷阱或被动成交。计算滚动窗口内价格变化绝对值与成交量之比，并与历史均值比较。输出[-1,1]，负值表示当前冲击比低于历史均值（价格变动小但成交量大），预示反转或停滞风险；正值表示高效推动。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_impact",
            name="Volume-Impact Ratio",
            display_name="成交量冲击比",
            description="衡量单位成交量对价格的推动效率，低冲击比可能暗示市场流动性陷阱或被动成交。计算滚动窗口内价格变化绝对值与成交量之比，并与历史均值比较。输出[-1,1]，负值表示当前冲击比低于历史均值（价格变动小但成交量大），预示反转或停滞风险；正值表示高效推动。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 价格变化绝对值
        price_chg = np.abs(close.diff())
        # 避免除以零，添加小量
        impact = price_chg / (volume + 1e-10)
        # 滚动均值和标准差
        window = 20
        impact_ma = impact.rolling(window).mean()
        impact_std = impact.rolling(window).std()
        # z-score
        z = (impact - impact_ma) / (impact_std + 1e-10)
        # 取负值：低冲击比（负z）表示异常小，用tanh映射到[-1,1]
        result = -np.tanh(z)
        return pd.Series(result, index=data.index)
