"""AI因子: 量价背离因子 | 置信:60% | 检测价格小幅变动但成交量异常放大或缩小的状态，反映市场分歧或蓄力。计算最近5周期价格变化百分比的绝对值与成交量变化率的比值，当比值极小（放量不涨）或极大（缩量急涨）时输出极端值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Price_Volume_Divergence(BaseFactor):
    """检测价格小幅变动但成交量异常放大或缩小的状态，反映市场分歧或蓄力。计算最近5周期价格变化百分比的绝对值与成交量变化率的比值，当比值极小（放量不涨）或极大（缩量急涨）时输出极端值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_price_volume_div",
            name="Price-Volume Divergence",
            display_name="量价背离因子",
            description="检测价格小幅变动但成交量异常放大或缩小的状态，反映市场分歧或蓄力。计算最近5周期价格变化百分比的绝对值与成交量变化率的比值，当比值极小（放量不涨）或极大（缩量急涨）时输出极端值。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd

        close = data['close']
        volume = data['volume']

        # 价格变化率 (5周期)
        price_chg = close.pct_change(5).abs()
        # 成交量变化率 (5周期)
        vol_chg = volume.pct_change(5).abs()

        # 避免除以零
        ratio = price_chg / (vol_chg + 1e-10)

        # 对ratio进行归一化: 使用滚动z-score然后tanh
        ratio_mean = ratio.rolling(50).mean()
        ratio_std = ratio.rolling(50).std()
        z = (ratio - ratio_mean) / (ratio_std + 1e-10)
        result = np.tanh(z * 0.5)  # 平滑到[-1,1]
        return pd.Series(result, index=data.index)
