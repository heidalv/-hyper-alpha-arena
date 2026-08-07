"""AI因子: 动量均值回归 | 置信:60% | 计算短期收益率（5日）与长期收益率（20日）的偏离程度，当短期动量过高（正偏离太大）时容易发生反转下跌，导致做多亏损；反之负偏离时可能反弹。输出值正向表示短线动量相对于长线过强，容易回调（负向因子）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Momentum_Mean_Reversion(BaseFactor):
    """计算短期收益率（5日）与长期收益率（20日）的偏离程度，当短期动量过高（正偏离太大）时容易发生反转下跌，导致做多亏损；反之负偏离时可能反弹。输出值正向表示短线动量相对于长线过强，容易回调（负向因子）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momrev",
            name="Momentum Mean Reversion",
            display_name="动量均值回归",
            description="计算短期收益率（5日）与长期收益率（20日）的偏离程度，当短期动量过高（正偏离太大）时容易发生反转下跌，导致做多亏损；反之负偏离时可能反弹。输出值正向表示短线动量相对于长线过强，容易回调（负向因子）。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        C = data['close']
        short_ret = C.pct_change(5)
        long_ret = C.pct_change(20)
        diff = short_ret - long_ret
        # 用滚动标准差标准化
        std = diff.rolling(50, min_periods=10).std().replace(0, np.nan)
        z = diff / std
        # 映射到[-1,1]，tanh平滑
        result = np.tanh(z * 0.5)
        return result.fillna(0).clip(-1, 1)
