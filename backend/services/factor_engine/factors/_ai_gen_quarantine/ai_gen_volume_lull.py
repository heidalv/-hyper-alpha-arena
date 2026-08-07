"""AI因子: 量能休整与价格停滞 | 置信:55% | 结合价格窄幅波动与成交量萎缩的特征。当价格变动微小且成交量持续低迷时，市场缺乏方向性驱动，易出现‘unknown regime’导致的亏损。因子负向表示低量停滞状态，应谨慎做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Lull_Price_Stagnation(BaseFactor):
    """结合价格窄幅波动与成交量萎缩的特征。当价格变动微小且成交量持续低迷时，市场缺乏方向性驱动，易出现‘unknown regime’导致的亏损。因子负向表示低量停滞状态，应谨慎做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_lull",
            name="Volume Lull & Price Stagnation",
            display_name="量能休整与价格停滞",
            description="结合价格窄幅波动与成交量萎缩的特征。当价格变动微小且成交量持续低迷时，市场缺乏方向性驱动，易出现‘unknown regime’导致的亏损。因子负向表示低量停滞状态，应谨慎做多。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        price_change = data['close'].pct_change().abs()
        price_volatility = price_change.rolling(10).mean()
        volume_avg = data['volume'].rolling(10).mean()
        volume_std = data['volume'].rolling(10).std()
        # 成交量萎缩：当前量低于均量1个标准差
        volume_ratio = (data['volume'] - volume_avg) / (volume_std + 1e-10)
        # 价格停滞：价格波动率低于其10日均值的0.5倍
        price_ratio = price_volatility / (price_volatility.rolling(20).mean() + 1e-10)
        # 组合：当两者都低时，因子为负
        combined = -np.tanh(price_ratio + volume_ratio)
        combined = combined.fillna(0).clip(-1, 1)
        return combined
