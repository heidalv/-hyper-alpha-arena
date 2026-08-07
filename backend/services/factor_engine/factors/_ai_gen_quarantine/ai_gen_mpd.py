"""AI因子: 均值偏离度 | 置信:60% | 计算当前收盘价相对于20期移动平均线的百分比偏离，并除以近期波动率（20期标准差），得到标准化偏离。然后用tanh映射到[-1,1]，正值表示价格远高于均线（超买风险），负值表示远低于均线（超卖机会）。旨在捕捉类似亏损中因过度偏离导致的回调止损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Mean_Price_Deviation(BaseFactor):
    """计算当前收盘价相对于20期移动平均线的百分比偏离，并除以近期波动率（20期标准差），得到标准化偏离。然后用tanh映射到[-1,1]，正值表示价格远高于均线（超买风险），负值表示远低于均线（超卖机会）。旨在捕捉类似亏损中因过度偏离导致的回调止损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mpd",
            name="Mean Price Deviation",
            display_name="均值偏离度",
            description="计算当前收盘价相对于20期移动平均线的百分比偏离，并除以近期波动率（20期标准差），得到标准化偏离。然后用tanh映射到[-1,1]，正值表示价格远高于均线（超买风险），负值表示远低于均线（超卖机会）。旨在捕捉类似亏损中因过度偏离导致的回调止损。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ma = close.rolling(20).mean()
        std = close.rolling(20).std()
        pct_dev = (close - ma) / ma
        # 标准化偏离 (类似z-score)
        dev_z = pct_dev / (std / ma + 1e-10)
        # 用tanh压缩到[-1,1]
        result = np.tanh(dev_z * 2)
        return result
