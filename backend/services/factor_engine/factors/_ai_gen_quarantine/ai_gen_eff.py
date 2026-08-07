"""AI因子: 效率比因子 | 置信:60% | 计算价格效率比（收盘价路径长度与净变化的比值），衡量趋势的稳定性。当效率比低时市场处于震荡（regime=unknown），该因子输出负值提示避免趋势跟踪；效率比高时输出正值提示顺势交易。基于过去20根K线计算，归一化至[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class EfficiencyRatioFactor(BaseFactor):
    """计算价格效率比（收盘价路径长度与净变化的比值），衡量趋势的稳定性。当效率比低时市场处于震荡（regime=unknown），该因子输出负值提示避免趋势跟踪；效率比高时输出正值提示顺势交易。基于过去20根K线计算，归一化至[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_eff",
            name="Efficiency Ratio Factor",
            display_name="效率比因子",
            description="计算价格效率比（收盘价路径长度与净变化的比值），衡量趋势的稳定性。当效率比低时市场处于震荡（regime=unknown），该因子输出负值提示避免趋势跟踪；效率比高时输出正值提示顺势交易。基于过去20根K线计算，归一化至[-1,1]。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        period = 20
        # 净变化
        net = close.diff(period).abs()
        # 路径长度：每个bar的high-low之和
        path = (high - low).rolling(period).sum()
        # 效率比
        er = net / (path + 1e-10)
        # 归一化：使用过去100天的均值和标准差
        er_mean = er.rolling(100).mean()
        er_std = er.rolling(100).std()
        z = (er - er_mean) / (er_std + 1e-10)
        # 用tanh映射到[-1,1]
        result = np.tanh(z)
        return result.fillna(0).clip(-1,1)
