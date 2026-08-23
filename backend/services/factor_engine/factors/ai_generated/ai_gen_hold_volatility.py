"""AI因子: 持仓波动率比值 | 置信:70% | 计算持仓期间价格波动率与平均真实波幅的比值，异常波动导致强制平仓"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Holdvolatilityratio(BaseFactor):
    """计算持仓期间价格波动率与平均真实波幅的比值，异常波动导致强制平仓"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_hold_volatility",
            name="HoldVolatilityRatio",
            display_name="持仓波动率比值",
            description="计算持仓期间价格波动率与平均真实波幅的比值，异常波动导致强制平仓",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        atr = pd.DataFrame({'hl':abs(data['high']-data['low']),'hc':abs(data['high']-data['close'].shift(1)),'lc':abs(data['low']-data['close'].shift(1))}).max(axis=1).rolling(14).mean()
        return (data['close'].pct_change().abs().rolling(20).std() / atr).astype(float).clip(-1,1)
