"""AI因子: 动量波动率比 | 置信:60% | 计算最近收益率与波动率的比值，衡量趋势的可靠性。比值低时趋势弱，易被假突破打止损；比值高时趋势强。在未知状态下，该比值通常接近0或负数。输出[-1,1]：正值表示强趋势，负值表示反向波动。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumVolatilityRatio(BaseFactor):
    """计算最近收益率与波动率的比值，衡量趋势的可靠性。比值低时趋势弱，易被假突破打止损；比值高时趋势强。在未知状态下，该比值通常接近0或负数。输出[-1,1]：正值表示强趋势，负值表示反向波动。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mvr",
            name="Momentum-Volatility Ratio",
            display_name="动量波动率比",
            description="计算最近收益率与波动率的比值，衡量趋势的可靠性。比值低时趋势弱，易被假突破打止损；比值高时趋势强。在未知状态下，该比值通常接近0或负数。输出[-1,1]：正值表示强趋势，负值表示反向波动。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算收益率（对数收益）
        returns = np.log(data['close'] / data['close'].shift(1))
        # 短期动量：过去5期收益和
        mom = returns.rolling(window=5, min_periods=1).sum()
        # 波动率：过去10期收益标准差
        vol = returns.rolling(window=10, min_periods=1).std()
        # 动量波动率比，并标准化
        ratio = mom / (vol + 1e-10)
        # 使用tanh映射到[-1,1]
        return pd.Series(np.tanh(ratio), index=data.index)
