"""AI因子: 布林带宽度挤压风险 | 置信:65% | 计算布林带宽度（带宽/中轨），当带宽低于历史20日分位数时认为市场处于低波动挤压状态，容易突发方向导致止损，返回负值；高波动时返回正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class BollingerBandWidthSqueeze(BaseFactor):
    """计算布林带宽度（带宽/中轨），当带宽低于历史20日分位数时认为市场处于低波动挤压状态，容易突发方向导致止损，返回负值；高波动时返回正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bbw_risk",
            name="Bollinger Band Width Squeeze",
            display_name="布林带宽度挤压风险",
            description="计算布林带宽度（带宽/中轨），当带宽低于历史20日分位数时认为市场处于低波动挤压状态，容易突发方向导致止损，返回负值；高波动时返回正值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        sma = close.rolling(20).mean()
        std = close.rolling(20).std()
        bandwidth = 2 * std / sma  # 相对带宽
        # 计算带宽的历史百分位（滚动20天）
        rank = bandwidth.rolling(20).apply(lambda x: (x[-1] - x.min()) / (x.max() - x.min() + 1e-10) * 2 - 1)
        result = rank.fillna(0)
        return pd.Series(result, index=data.index)
