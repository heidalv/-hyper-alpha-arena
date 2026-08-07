"""AI因子: 市场不确定性 | 置信:60% | 基于波动率比率与成交量异常，识别市场状态不明的高风险区域。计算过去20周期波动率与过去5周期波动率的比值，结合成交量相对于过去20周期均值的标准差。比值高且成交量异常时输出负值（-1），反之正值（+1）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MarketUncertainty(BaseFactor):
    """基于波动率比率与成交量异常，识别市场状态不明的高风险区域。计算过去20周期波动率与过去5周期波动率的比值，结合成交量相对于过去20周期均值的标准差。比值高且成交量异常时输出负值（-1），反之正值（+1）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mkt_uncertainty",
            name="Market Uncertainty",
            display_name="市场不确定性",
            description="基于波动率比率与成交量异常，识别市场状态不明的高风险区域。计算过去20周期波动率与过去5周期波动率的比值，结合成交量相对于过去20周期均值的标准差。比值高且成交量异常时输出负值（-1），反之正值（+1）。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算收益率
        returns = data['close'].pct_change()
        # 波动率：20日滚动标准差 vs 5日滚动标准差
        vol_20 = returns.rolling(20).std()
        vol_5 = returns.rolling(5).std()
        vol_ratio = vol_5 / vol_20
        # 成交量异常：当前成交量 vs 20日均值的标准差倍数
        vol_20_mean = data['volume'].rolling(20).mean()
        vol_20_std = data['volume'].rolling(20).std()
        vol_z = (data['volume'] - vol_20_mean) / (vol_20_std + 1e-8)
        # 组合：当vol_ratio>1.5且vol_z>2时，不确定性高
        uncertainty = -1.0 * ((vol_ratio > 1.5) & (vol_z > 2.0)).astype(float)
        # 平滑并归一化到[-1,1]
        result = uncertainty.rolling(3).mean().fillna(0).clip(-1,1)
        return result
