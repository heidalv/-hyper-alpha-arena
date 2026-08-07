"""AI因子: 流动性冲击检测 | 置信:60% | 检测成交量相对于过去20日均量的异常偏离，成交量骤增或骤减常伴随市场结构突变，容易导致止损和超时。将偏离程度映射到[-1,1]，负值表示异常。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Liquidityshockdetector(BaseFactor):
    """检测成交量相对于过去20日均量的异常偏离，成交量骤增或骤减常伴随市场结构突变，容易导致止损和超时。将偏离程度映射到[-1,1]，负值表示异常。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liquidity_shock",
            name="LiquidityShockDetector",
            display_name="流动性冲击检测",
            description="检测成交量相对于过去20日均量的异常偏离，成交量骤增或骤减常伴随市场结构突变，容易导致止损和超时。将偏离程度映射到[-1,1]，负值表示异常。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        volume = data['volume']
        vol_ma20 = volume.rolling(window=20, min_periods=20).mean()
        ratio = volume / vol_ma20
        # 当ratio在[0.5,2]之间认为正常，映射到1；否则线性映射到-1
        # 使用分段函数: 若0.5<ratio<2, score=1-((ratio-1.25)/0.75)^2? 简化
        lower = 0.5
        upper = 2.0
        mid = 1.25
        def map_ratio(r):
            if pd.isna(r):
                return 0
            if r < lower:
                # 从lower到0线性映射到0到-1
                return -1.0 * (1 - r / lower)
            elif r > upper:
                # 从upper到+∞映射到-1到0? 使用指数衰减
                return -1.0 * (1 - np.exp(-(r - upper)))
            else:
                # 正常区间: 在lower到mid段上升, mid到upper段下降
                if r < mid:
                    return (r - lower) / (mid - lower) * 2 - 1  # 映射到[-1,1]
                else:
                    return 1 - (r - mid) / (upper - mid) * 2  # 从1下降到-1
        result = ratio.apply(map_ratio)
        return result
