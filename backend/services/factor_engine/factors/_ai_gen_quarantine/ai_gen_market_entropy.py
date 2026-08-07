"""AI因子: 市场熵值因子 | 置信:60% | 基于价格在短期、中期、长期均线之间的缠绕程度（均线排列混乱度），当三条均线相互靠近时熵值高，市场缺乏方向，因子负向；排列整齐时因子正向"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MarketEntropy(BaseFactor):
    """基于价格在短期、中期、长期均线之间的缠绕程度（均线排列混乱度），当三条均线相互靠近时熵值高，市场缺乏方向，因子负向；排列整齐时因子正向"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_market_entropy",
            name="MarketEntropy",
            display_name="市场熵值因子",
            description="基于价格在短期、中期、长期均线之间的缠绕程度（均线排列混乱度），当三条均线相互靠近时熵值高，市场缺乏方向，因子负向；排列整齐时因子正向",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        # 计算短期、中期、长期均线
        ma5 = close.rolling(5).mean()
        ma20 = close.rolling(20).mean()
        ma60 = close.rolling(60).mean()
        # 计算均线之间的归一化距离
        diff_5_20 = np.abs(ma5 - ma20) / (ma5 + ma20 + 1e-10)
        diff_5_60 = np.abs(ma5 - ma60) / (ma5 + ma60 + 1e-10)
        diff_20_60 = np.abs(ma20 - ma60) / (ma20 + ma60 + 1e-10)
        # 熵值：三个距离的平均值，越小表示越混乱
        entropy = (diff_5_20 + diff_5_60 + diff_20_60) / 3.0
        # 熵值越大（均线分离）趋势越明显，因子为正；熵值越小（缠绕）为负
        # 使用滚动窗口的百分位映射到[-1,1]
        entropy_rank = entropy.rolling(60, min_periods=30).rank(pct=True).fillna(0.5)
        result = (entropy_rank - 0.5) * 2.0
        return result.fillna(0.0)
