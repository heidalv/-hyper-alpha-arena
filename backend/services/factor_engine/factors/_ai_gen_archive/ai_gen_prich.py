"""AI因子: 价格通道一致性 | 置信:65% | 通过线性回归R²衡量过去N周期价格走势的直线拟合程度，反映价格运动的稳定性。R²越高（接近1）表示趋势稳定，R²越低（接近0）表示价格随机波动。返回[-1,1]：+1表示非常稳定的趋势（向上或向下），-1表示极度混乱的震荡。可用于识别适合趋势跟踪的市场状态。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Pricechannelconsistency(BaseFactor):
    """通过线性回归R²衡量过去N周期价格走势的直线拟合程度，反映价格运动的稳定性。R²越高（接近1）表示趋势稳定，R²越低（接近0）表示价格随机波动。返回[-1,1]：+1表示非常稳定的趋势（向上或向下），-1表示极度混乱的震荡。可用于识别适合趋势跟踪的市场状态。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_prich",
            name="PriceChannelConsistency",
            display_name="价格通道一致性",
            description="通过线性回归R²衡量过去N周期价格走势的直线拟合程度，反映价格运动的稳定性。R²越高（接近1）表示趋势稳定，R²越低（接近0）表示价格随机波动。返回[-1,1]：+1表示非常稳定的趋势（向上或向下），-1表示极度混乱的震荡。可用于识别适合趋势跟踪的市场状态。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        n = 20
        close = data['close'].values
        result = np.full(len(close), np.nan)
        for i in range(n, len(close)):
            y = close[i-n:i]
            x = np.arange(n)
            # 计算线性回归R²
            corr = np.corrcoef(x, y)[0, 1]
            r2 = corr ** 2
            # 符号：根据斜率方向
            slope = np.polyfit(x, y, 1)[0]
            signed_r2 = r2 * (1 if slope > 0 else -1)
            result[i] = signed_r2
        # 映射到[-1,1]：R²本身在[0,1]，乘方向后[-1,1]
        return pd.Series(result, index=data.index)
