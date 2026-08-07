"""AI因子: 布林带反转因子 | 置信:60% | 当价格突破布林带上轨或下轨且带宽（标准差/中轨）处于历史低位时，预期均值回归。价格超出上轨且带宽低于过去100日20分位时做空(-1)，超出下轨且带宽低位时做多(+1)，否则为0。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class BollingerBandReversal(BaseFactor):
    """当价格突破布林带上轨或下轨且带宽（标准差/中轨）处于历史低位时，预期均值回归。价格超出上轨且带宽低于过去100日20分位时做空(-1)，超出下轨且带宽低位时做多(+1)，否则为0。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bbcontrarian",
            name="Bollinger Band Reversal",
            display_name="布林带反转因子",
            description="当价格突破布林带上轨或下轨且带宽（标准差/中轨）处于历史低位时，预期均值回归。价格超出上轨且带宽低于过去100日20分位时做空(-1)，超出下轨且带宽低位时做多(+1)，否则为0。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        # 布林带参数
        n = 20
        ma = data['close'].rolling(n).mean()
        std = data['close'].rolling(n).std()
        upper = ma + 2 * std
        lower = ma - 2 * std
        # 带宽 = std / ma
        bandwidth = std / ma
        # 带宽的历史分位（过去100日）
        quantile_20 = bandwidth.rolling(100).quantile(0.2)
        # 价格位置
        above_upper = data['close'] > upper
        below_lower = data['close'] < lower
        # 条件：突破且带宽低位
        condition_long = below_lower & (bandwidth < quantile_20)
        condition_short = above_upper & (bandwidth < quantile_20)
        result = pd.Series(0, index=data.index)
        result[condition_long] = 1
        result[condition_short] = -1
        return result
