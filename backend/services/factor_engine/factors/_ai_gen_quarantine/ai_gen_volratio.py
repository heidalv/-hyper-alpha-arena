"""AI因子: 量价背离 | 置信:60% | 结合成交量和价格变化，检测量能是否支持当前趋势。计算成交量相对于过去20日均量的变化率与价格变化率的差值，当价格上涨但缩量时做多风险大。输出[-1,1]，负值表示上涨缩量。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class volume_price_divergence(BaseFactor):
    """结合成交量和价格变化，检测量能是否支持当前趋势。计算成交量相对于过去20日均量的变化率与价格变化率的差值，当价格上涨但缩量时做多风险大。输出[-1,1]，负值表示上涨缩量。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volratio",
            name="volume_price_divergence",
            display_name="量价背离",
            description="结合成交量和价格变化，检测量能是否支持当前趋势。计算成交量相对于过去20日均量的变化率与价格变化率的差值，当价格上涨但缩量时做多风险大。输出[-1,1]，负值表示上涨缩量。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        # 价格变化率 (1日)
        price_ret = close.pct_change()
        # 成交量变化率相对于20日均量
        vol_ma20 = volume.rolling(20).mean()
        vol_ratio = volume / vol_ma20
        vol_ratio = vol_ratio.fillna(1).replace([np.inf, -np.inf], 1)
        # 构造量价背离：当价格涨而量缩时，因子为负；价格跌而量增时，因子为正
        # 使用 (price_ret * (vol_ratio - 1)) 来测量背离，然后tanh
        # 为避免小波动，先标准化
        combined = price_ret * (vol_ratio - 1)
        # 用滚动标准差归一化
        std_combined = combined.rolling(20).std()
        normalized = combined / std_combined
        normalized = normalized.fillna(0).replace([np.inf, -np.inf], 0)
        result = np.tanh(normalized)
        return result
