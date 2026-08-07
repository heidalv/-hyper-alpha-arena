"""AI因子: 量波比 | 置信:60% | 计算近期价格波动率与成交量的比值变化，当比值异常偏离均值时，表明市场状态发生切换，容易引发止损/止盈失败。使用20日波动率与20日平均成交量的比值，并与60日均值比较，标准化到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Volatility_Ratio(BaseFactor):
    """计算近期价格波动率与成交量的比值变化，当比值异常偏离均值时，表明市场状态发生切换，容易引发止损/止盈失败。使用20日波动率与20日平均成交量的比值，并与60日均值比较，标准化到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vvr",
            name="Volume Volatility Ratio",
            display_name="量波比",
            description="计算近期价格波动率与成交量的比值变化，当比值异常偏离均值时，表明市场状态发生切换，容易引发止损/止盈失败。使用20日波动率与20日平均成交量的比值，并与60日均值比较，标准化到[-1,1]。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算对数收益率
        returns = np.log(data['close'] / data['close'].shift(1))
        # 20日波动率（标准差）
        vol_20 = returns.rolling(20).std()
        # 20日平均成交量
        avg_vol_20 = data['volume'].rolling(20).mean()
        # 比率
        ratio = vol_20 / (avg_vol_20 + 1e-10)
        # 60日均值与标准差
        ratio_mean = ratio.rolling(60).mean()
        ratio_std = ratio.rolling(60).std()
        # 标准化到[-1,1]
        z = (ratio - ratio_mean) / (ratio_std + 1e-10)
        result = np.clip(z / 3.0, -1, 1)  # 3sigma截断
        return result.fillna(0)
