"""AI因子: 波动率突发指数 | 置信:70% | 衡量当前短期波动率相对于近期平均波动率的异常程度。当波动率突然飙升时，市场状态可能从有序变为无序，导致止损和持仓超时亏损。正值表示波动率异常高，负值表示异常低。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Explosion_Index(BaseFactor):
    """衡量当前短期波动率相对于近期平均波动率的异常程度。当波动率突然飙升时，市场状态可能从有序变为无序，导致止损和持仓超时亏损。正值表示波动率异常高，负值表示异常低。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vix",
            name="Volatility Explosion Index",
            display_name="波动率突发指数",
            description="衡量当前短期波动率相对于近期平均波动率的异常程度。当波动率突然飙升时，市场状态可能从有序变为无序，导致止损和持仓超时亏损。正值表示波动率异常高，负值表示异常低。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算5分钟和20分钟收益率标准差
        ret = data['close'].pct_change()
        vol_short = ret.rolling(5).std()
        vol_long = ret.rolling(20).std()
        # 波动率比率，再经tanh压缩到[-1,1]
        ratio = vol_short / vol_long.replace(0, np.nan)
        ratio = ratio.fillna(1.0).clip(0.5, 2.0)  # 防止极端值
        # 中心化并缩放
        result = 2 * (ratio - 1) / (2 - 0.5) * 2 - 1
        result = result.clip(-1, 1)
        return result
