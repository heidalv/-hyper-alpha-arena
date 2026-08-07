"""AI因子: 多周期趋势一致性 | 置信:65% | 计算3个时间尺度（5、15、60周期）的动量方向，如果三个方向一致（均为正或均为负）则信号强，否则信号弱。方向通过当前价格与滚动均值比较确定。返回[-1,1]，+1表示一致看多，-1一致看空，0表示分歧。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiScaleTrendConsistency(BaseFactor):
    """计算3个时间尺度（5、15、60周期）的动量方向，如果三个方向一致（均为正或均为负）则信号强，否则信号弱。方向通过当前价格与滚动均值比较确定。返回[-1,1]，+1表示一致看多，-1一致看空，0表示分歧。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_consistency",
            name="Multi-scale Trend Consistency",
            display_name="多周期趋势一致性",
            description="计算3个时间尺度（5、15、60周期）的动量方向，如果三个方向一致（均为正或均为负）则信号强，否则信号弱。方向通过当前价格与滚动均值比较确定。返回[-1,1]，+1表示一致看多，-1一致看空，0表示分歧。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        # 三个周期SMA
        sma5 = close.rolling(5, min_periods=5).mean()
        sma15 = close.rolling(15, min_periods=15).mean()
        sma60 = close.rolling(60, min_periods=60).mean()
        # 方向: 当前价格高于均线为1，低于为-1
        dir5 = np.sign(close - sma5)
        dir15 = np.sign(close - sma15)
        dir60 = np.sign(close - sma60)
        sum_dir = dir5 + dir15 + dir60
        # 如果三个方向相同则sum为3或-3，否则中间值
        result = sum_dir / 3.0
        result = result.fillna(0)
        return result
