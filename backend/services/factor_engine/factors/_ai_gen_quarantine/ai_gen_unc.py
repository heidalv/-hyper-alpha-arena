"""AI因子: 市场不确定性指数 | 置信:60% | 基于滚动收益率标准差的变化率衡量市场不确定性。当波动率急剧变化时，市场状态不明概率高，因子值趋于-1；当波动率稳定时，因子值趋于+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MarketUncertaintyIndex(BaseFactor):
    """基于滚动收益率标准差的变化率衡量市场不确定性。当波动率急剧变化时，市场状态不明概率高，因子值趋于-1；当波动率稳定时，因子值趋于+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unc",
            name="Market Uncertainty Index",
            display_name="市场不确定性指数",
            description="基于滚动收益率标准差的变化率衡量市场不确定性。当波动率急剧变化时，市场状态不明概率高，因子值趋于-1；当波动率稳定时，因子值趋于+1。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change()
        vol_short = ret.rolling(5).std()
        vol_long = ret.rolling(20).std()
        vol_ratio = (vol_short / vol_long).replace([np.inf, -np.inf], np.nan)
        # 将vol_ratio标准化到[-1,1]：使用log变换 + tanh
        log_ratio = np.log(vol_ratio.clip(0.1, 10))
        result = -np.tanh((log_ratio - log_ratio.mean()) / log_ratio.std())
        return result.fillna(0).clip(-1, 1)
