"""AI因子: 持仓超时惩罚 | 置信:60% | 模拟持仓时间过长导致的衰减效应，当价格长时间横向或微小波动时，降低做多意愿，避免max_hold_timeout类亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Hold_Time_Penalty(BaseFactor):
    """模拟持仓时间过长导致的衰减效应，当价格长时间横向或微小波动时，降低做多意愿，避免max_hold_timeout类亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timepenalty",
            name="Hold_Time_Penalty",
            display_name="持仓超时惩罚",
            description="模拟持仓时间过长导致的衰减效应，当价格长时间横向或微小波动时，降低做多意愿，避免max_hold_timeout类亏损。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        # 计算累积价格变化绝对值（25日滑动窗口）
        cumulative_change = (close / close.shift(1) - 1).rolling(25).apply(lambda x: np.abs(x).sum(), raw=True)
        # 如果累积变化很小（低于历史20分位），表示市场停滞，惩罚信号
        low_thresh = cumulative_change.rolling(60).quantile(0.2)
        # 惩罚因子：累积变化越小，信号越负（偏向空头或多头减仓）
        penalty = np.where(cumulative_change < low_thresh, cumulative_change / low_thresh, 1.0)
        # 用短均线斜率作为基础信号
        slope = close.rolling(10).mean() / close.rolling(20).mean() - 1
        signal = slope * penalty
        signal = np.clip(signal * 10, -1, 1)
        return pd.Series(signal, index=data.index)
