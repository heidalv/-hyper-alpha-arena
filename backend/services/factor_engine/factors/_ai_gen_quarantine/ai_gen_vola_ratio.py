"""AI因子: 波动率异常比 | 置信:70% | 短期波动率与长期波动率之比，捕捉波动率突发扩张或收缩。当短期波动率远高于长期时，市场可能处于不稳定状态，导致未知模式下的亏损。值域[-1,+1]，正值表示异常高波动，负值表示低波动稳定。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Ratio(BaseFactor):
    """短期波动率与长期波动率之比，捕捉波动率突发扩张或收缩。当短期波动率远高于长期时，市场可能处于不稳定状态，导致未知模式下的亏损。值域[-1,+1]，正值表示异常高波动，负值表示低波动稳定。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vola_ratio",
            name="Volatility Ratio",
            display_name="波动率异常比",
            description="短期波动率与长期波动率之比，捕捉波动率突发扩张或收缩。当短期波动率远高于长期时，市场可能处于不稳定状态，导致未知模式下的亏损。值域[-1,+1]，正值表示异常高波动，负值表示低波动稳定。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 使用20日滚动标准差作为短期波动率，60日作为长期
        returns = data['close'].pct_change().fillna(0)
        short_vol = returns.rolling(20).std()
        long_vol = returns.rolling(60).std()
        # 防止除零
        ratio = short_vol / (long_vol + 1e-10)
        # 使用log变换后归一化到[-1,1]
        log_ratio = np.log(ratio + 1e-10)
        # 假设log_ratio 99%落在[-2,2]之间
        result = np.clip(log_ratio / 2.0, -1, 1)
        return result.fillna(0)
