"""AI因子: 价格位置回归因子 | 置信:60% | 计算当前价格在近期（20周期）最高最低区间内的相对位置，并用成交量加权调整。当价格处于高位且成交量低于均值时，意味着上涨动能不足，容易反转下跌，因子输出负值；反之低位放量则输出正值。旨在捕捉假突破和均值回归机会。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Position_Regression(BaseFactor):
    """计算当前价格在近期（20周期）最高最低区间内的相对位置，并用成交量加权调整。当价格处于高位且成交量低于均值时，意味着上涨动能不足，容易反转下跌，因子输出负值；反之低位放量则输出正值。旨在捕捉假突破和均值回归机会。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pos_reg",
            name="Position_Regression",
            display_name="价格位置回归因子",
            description="计算当前价格在近期（20周期）最高最低区间内的相对位置，并用成交量加权调整。当价格处于高位且成交量低于均值时，意味着上涨动能不足，容易反转下跌，因子输出负值；反之低位放量则输出正值。旨在捕捉假突破和均值回归机会。",
            category="mean_reversion",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        window = 20
        rolling_high = high.rolling(window).max()
        rolling_low = low.rolling(window).min()
        pos = (close - rolling_low) / (rolling_high - rolling_low + 1e-10)
        vol_ma = volume.rolling(window).mean()
        vol_ratio = volume / (vol_ma + 1e-10)
        # 当价格高位且成交量低时，因子负；价格低位且成交量高时，因子正
        raw = (0.5 - pos) * np.log(vol_ratio + 1)
        # 标准化到[-1,1]
        result = pd.Series(np.tanh(raw), index=data.index)
        return result
