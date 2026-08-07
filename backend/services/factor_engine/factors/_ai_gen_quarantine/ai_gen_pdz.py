"""AI因子: 价格偏离标准差 | 置信:60% | 计算当前收盘价相对于过去30日均值的偏离程度（以标准差为单位），并映射到[-1,1]。正偏离过大（>2σ）或负偏离过大（<-2σ）时，可能引起回调或反转，此时市场处于极端状态，容易触发止损或超时。使用tanh压缩避免极端值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PriceDeviationZscore(BaseFactor):
    """计算当前收盘价相对于过去30日均值的偏离程度（以标准差为单位），并映射到[-1,1]。正偏离过大（>2σ）或负偏离过大（<-2σ）时，可能引起回调或反转，此时市场处于极端状态，容易触发止损或超时。使用tanh压缩避免极端值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pdz",
            name="Price_Deviation_Zscore",
            display_name="价格偏离标准差",
            description="计算当前收盘价相对于过去30日均值的偏离程度（以标准差为单位），并映射到[-1,1]。正偏离过大（>2σ）或负偏离过大（<-2σ）时，可能引起回调或反转，此时市场处于极端状态，容易触发止损或超时。使用tanh压缩避免极端值。",
            category="mean_reversion",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        window = 30
        mean = close.rolling(window).mean()
        std = close.rolling(window).std()
        z = (close - mean) / (std + 1e-9)
        # 使用tanh将zscore映射到[-1,1]
        result = np.tanh(z)
        return result.fillna(0).clip(-1, 1)
