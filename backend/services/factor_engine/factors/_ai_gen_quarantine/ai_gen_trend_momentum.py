"""AI因子: 趋势动量 | 置信:70% | 基于过去20日收益率均值与标准差之比（类似夏普比率），通过tanh映射到[-1,1]。正值表示强趋势且方向向上，负值表示强趋势向下，接近0表示震荡。该因子在regime=unknown时倾向于接近0，从而避免做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Momentum(BaseFactor):
    """基于过去20日收益率均值与标准差之比（类似夏普比率），通过tanh映射到[-1,1]。正值表示强趋势且方向向上，负值表示强趋势向下，接近0表示震荡。该因子在regime=unknown时倾向于接近0，从而避免做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_momentum",
            name="Trend Momentum",
            display_name="趋势动量",
            description="基于过去20日收益率均值与标准差之比（类似夏普比率），通过tanh映射到[-1,1]。正值表示强趋势且方向向上，负值表示强趋势向下，接近0表示震荡。该因子在regime=unknown时倾向于接近0，从而避免做多。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        window = 20
        returns = data['close'].pct_change()
        rolling_mean = returns.rolling(window).mean()
        rolling_std = returns.rolling(window).std()
        # 避免除零
        rolling_std = rolling_std.replace(0, np.nan)
        t_stat = rolling_mean / rolling_std * np.sqrt(window)
        # 用tanh压缩到[-1,1]
        result = np.tanh(t_stat)
        result = result.fillna(0).clip(-1,1)
        return result
