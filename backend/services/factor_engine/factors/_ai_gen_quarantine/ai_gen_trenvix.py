"""AI因子: 趋势明确度指数 | 置信:65% | 通过比较短期（5日）和长期（20日）价格变化的方向与强度，衡量当前市场是否具有清晰的单边趋势。当短长期方向一致且强度足够时输出正值，反之输出负值，以规避震荡不明的‘未知’市场状态。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendClarityIndex(BaseFactor):
    """通过比较短期（5日）和长期（20日）价格变化的方向与强度，衡量当前市场是否具有清晰的单边趋势。当短长期方向一致且强度足够时输出正值，反之输出负值，以规避震荡不明的‘未知’市场状态。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trenvix",
            name="Trend Clarity Index",
            display_name="趋势明确度指数",
            description="通过比较短期（5日）和长期（20日）价格变化的方向与强度，衡量当前市场是否具有清晰的单边趋势。当短长期方向一致且强度足够时输出正值，反之输出负值，以规避震荡不明的‘未知’市场状态。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        # 短期和长期收益率
        ret_short = close.pct_change(5)
        ret_long = close.pct_change(20)
        # 滚动均值和标准差，用于标准化强度
        mean_short = ret_short.rolling(50, min_periods=20).mean()
        std_short = ret_short.rolling(50, min_periods=20).std()
        mean_long = ret_long.rolling(50, min_periods=20).mean()
        std_long = ret_long.rolling(50, min_periods=20).std()
        # z-score标准化
        z_short = (ret_short - mean_short) / (std_short + 1e-10)
        z_long = (ret_long - mean_long) / (std_long + 1e-10)
        # 方向一致性：乘积为正表示同向，为负表示反向
        direction = np.sign(z_short * z_long)
        # 强度：取两个z-score绝对值的均值，再乘以方向
        strength = (np.abs(z_short) + np.abs(z_long)) / 2.0
        raw = direction * strength
        # 使用tanh映射到[-1,1]
        result = np.tanh(raw)
        return result
