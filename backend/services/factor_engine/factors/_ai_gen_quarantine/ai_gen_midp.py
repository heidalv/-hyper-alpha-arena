"""AI因子: 价格中枢偏离 | 置信:60% | 计算当前价格在最近N根K线最高最低区间内的相对位置，当价格接近区间中心（0.4~0.6）时，表示缺乏明确突破方向，做多易失败，因子输出负值；价格靠近区间边界时输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Mid_Price_Proximity(BaseFactor):
    """计算当前价格在最近N根K线最高最低区间内的相对位置，当价格接近区间中心（0.4~0.6）时，表示缺乏明确突破方向，做多易失败，因子输出负值；价格靠近区间边界时输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_midp",
            name="Mid-Price Proximity",
            display_name="价格中枢偏离",
            description="计算当前价格在最近N根K线最高最低区间内的相对位置，当价格接近区间中心（0.4~0.6）时，表示缺乏明确突破方向，做多易失败，因子输出负值；价格靠近区间边界时输出正值。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        window = 20
        rolling_high = high.rolling(window=window).max()
        rolling_low = low.rolling(window=window).min()
        range_ = rolling_high - rolling_low
        # 相对位置: 0~1
        position = (close - rolling_low) / range_.replace(0, np.nan)
        # 计算偏离中间的程度: 0.5 - abs(position - 0.5) 范围[0,0.5]，然后映射到[-1,1]
        deviation = 0.5 - abs(position - 0.5)
        # 偏离越大（越接近边界）得正值；偏离越小（接近中心）得负值
        result = deviation * 2 - 1  # 将[0,0.5]映射到[-1,0]? 实际上偏差0 -> 中心 -> 输出-1; 偏差0.5 -> 边界 -> 输出0? 需要调整
        # 更直接的：中心附近给负，边界给正。用position-0.5的绝对值，然后反向。
        # 采用: result = 1 - 2 * abs(position - 0.5) ，这样中心0.5时result=1（正），边界0或1时result=-1（负）。
        # 但我们要做空/多？从错误模式看，在中心区域做多容易亏，所以中心应给负。修正：
        result = 2 * abs(position - 0.5) - 1  # 中心0.5->0*2-1=-1; 边界0或1->1*2-1=1。正好中心为负，边界为正。
        return result
