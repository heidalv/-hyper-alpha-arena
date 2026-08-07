"""AI因子: 流动性枯竭预警 | 置信:55% | 成交量和价格波动率同时下降表明流动性不足，价格容易受到大单影响导致异常波动，不利于止损精确执行。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityDryupWarning(BaseFactor):
    """成交量和价格波动率同时下降表明流动性不足，价格容易受到大单影响导致异常波动，不利于止损精确执行。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liquidity_dryup",
            name="Liquidity_Dryup_Warning",
            display_name="流动性枯竭预警",
            description="成交量和价格波动率同时下降表明流动性不足，价格容易受到大单影响导致异常波动，不利于止损精确执行。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 成交量变化率（5日均量 vs 20日均量）
        vol_5 = data['volume'].rolling(5).mean()
        vol_20 = data['volume'].rolling(20).mean()
        vol_ratio = vol_5 / vol_20
        # 价格波动率（20日最高最低波动幅度）
        price_range = (data['high'].rolling(20).max() - data['low'].rolling(20).min()) / data['close'].rolling(20).mean()
        # 成交量萎缩且波动率降低
        low_liquidity = (vol_ratio < 0.8) & (price_range < price_range.rolling(50).median() * 0.7)
        # 映射为负值表示流动性枯竭时做空风险大
        result = -low_liquidity.astype(float)
        # 平滑处理
        result = result.rolling(3).mean().fillna(0).clip(-1, 1)
        return result
