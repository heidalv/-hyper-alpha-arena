"""AI因子: 量价同步停滞 | 置信:60% | 当成交量萎缩且价格波动率同步降低时，市场处于休眠状态，策略容易反复止损或超时。该因子结合量价变化：计算成交量20日均线的相对变化与价格波动率衰减的乘积，再标准化到[-1,1]，负值表示需要回避。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeVolatilityStagnation(BaseFactor):
    """当成交量萎缩且价格波动率同步降低时，市场处于休眠状态，策略容易反复止损或超时。该因子结合量价变化：计算成交量20日均线的相对变化与价格波动率衰减的乘积，再标准化到[-1,1]，负值表示需要回避。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unkn_vol_vol",
            name="VolumeVolatilityStagnation",
            display_name="量价同步停滞",
            description="当成交量萎缩且价格波动率同步降低时，市场处于休眠状态，策略容易反复止损或超时。该因子结合量价变化：计算成交量20日均线的相对变化与价格波动率衰减的乘积，再标准化到[-1,1]，负值表示需要回避。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算成交量变化率：当前成交量与20日均量的比值-1
        vol_ma = data['volume'].rolling(20).mean()
        vol_ratio = (data['volume'] / vol_ma) - 1  # 正值表示放量
        # 价格波动率衰减（同第一个因子）
        ret = np.log(data['close'] / data['close'].shift(1))
        vol_short = ret.rolling(5).std()
        vol_long = ret.rolling(20).std()
        vol_decay = (vol_short / (vol_long + 1e-10)) - 1  # 负值表示波动衰减
        # 乘积：量缩且波动衰减时乘积为正（负*负=正），我们希望因子为负，所以取反
        product = - vol_ratio * vol_decay
        # 标准化到[-1,1]用tanh
        factor = np.tanh(product * 2)  # 调整灵敏度
        factor = factor.fillna(0)
        return factor
