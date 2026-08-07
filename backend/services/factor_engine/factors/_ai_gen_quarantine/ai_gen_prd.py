"""AI因子: 价格偏离均值 | 置信:60% | 计算当前收盘价相对于过去20日移动平均线的百分比偏离，并考虑波动率调整。高偏离（不论正负）但低趋势时易发生反转亏损。使用20日均线和标准差，输出(-1,1)。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Price_Deviation_Mean(BaseFactor):
    """计算当前收盘价相对于过去20日移动平均线的百分比偏离，并考虑波动率调整。高偏离（不论正负）但低趋势时易发生反转亏损。使用20日均线和标准差，输出(-1,1)。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_prd",
            name="Price_Deviation_Mean",
            display_name="价格偏离均值",
            description="计算当前收盘价相对于过去20日移动平均线的百分比偏离，并考虑波动率调整。高偏离（不论正负）但低趋势时易发生反转亏损。使用20日均线和标准差，输出(-1,1)。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            import pandas as pd
            import numpy as np
            close = data['close']
            ma20 = close.rolling(20).mean()
            std20 = close.rolling(20).std()
            # 偏离度 = (close - ma20) / std20
            deviation = (close - ma20) / std20.replace(0, np.nan)
            deviation = deviation.replace([np.inf, -np.inf], np.nan).fillna(0)
            # 用tanh限幅到[-1,1]
            result = np.tanh(deviation / 2)  # 除以2使±2σ映射到~0.96
            return result.fillna(0)
