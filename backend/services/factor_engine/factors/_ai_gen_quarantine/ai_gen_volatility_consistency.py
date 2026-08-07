"""AI因子: 波动率一致性指数 | 置信:55% | 通过计算收益率序列的标准差与平均绝对收益之比，衡量波动率的稳定性。当比值异常大时，表示波动不连续，市场状态混乱，输出负值；反之输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityConsistencyIndex(BaseFactor):
    """通过计算收益率序列的标准差与平均绝对收益之比，衡量波动率的稳定性。当比值异常大时，表示波动不连续，市场状态混乱，输出负值；反之输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_consistency",
            name="Volatility Consistency Index",
            display_name="波动率一致性指数",
            description="通过计算收益率序列的标准差与平均绝对收益之比，衡量波动率的稳定性。当比值异常大时，表示波动不连续，市场状态混乱，输出负值；反之输出正值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        returns = data['close'].pct_change().dropna()
        # 滚动窗口20期
        window = 20
        rolling_std = returns.rolling(window).std()
        rolling_mean_abs = returns.abs().rolling(window).mean()
        # 防止除零
        ratio = rolling_std / (rolling_mean_abs + 1e-10)
        # 比率理论上在1附近，异常大时表示波动不连续
        signal = -np.tanh(2 * (ratio - 1) * 5)
        return signal.reindex(data.index).fillna(0)
