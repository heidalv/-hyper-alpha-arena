"""AI因子: 趋势衰竭信号 | 置信:60% | 通过计算当前收盘价相对于过去N根K线最高最低的位置，并结合动量衰减（短期收益率与长期收益率之比），判断趋势是否衰竭。当价格处于极端位置且动量减弱时，输出负向信号（-1），反之当趋势健康时输出正向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Exhaustion_Signal(BaseFactor):
    """通过计算当前收盘价相对于过去N根K线最高最低的位置，并结合动量衰减（短期收益率与长期收益率之比），判断趋势是否衰竭。当价格处于极端位置且动量减弱时，输出负向信号（-1），反之当趋势健康时输出正向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trendexhaust",
            name="Trend Exhaustion Signal",
            display_name="趋势衰竭信号",
            description="通过计算当前收盘价相对于过去N根K线最高最低的位置，并结合动量衰减（短期收益率与长期收益率之比），判断趋势是否衰竭。当价格处于极端位置且动量减弱时，输出负向信号（-1），反之当趋势健康时输出正向信号。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        # 过去20天高低区间
        rolling_high = close.rolling(20).max()
        rolling_low = close.rolling(20).min()
        # 价格位置 0~1
        range_ = rolling_high - rolling_low
        position = (close - rolling_low) / (range_ + 1e-10)  # 0到1
        # 动量：短期收益率（5天）和长期收益率（20天）
        ret_short = close.pct_change(5)
        ret_long = close.pct_change(20)
        # 动量衰减：短期动量相对长期动量的比值，如果短期比长期小很多，表示衰竭
        momentum_ratio = ret_short / (ret_long.abs() + 1e-10)  # 注意符号
        # 对位置进行变换，极端位置（靠近0或1）警示
        extreme_position = (position - 0.5).abs() * 2  # 0~1
        # 综合：当位置极端且动量衰减时，负向
        # 定义信号：趋势衰竭 = (极端位置 > 0.8) & (动量比值 < 0.5 或 ret_short < 0 且 ret_long > 0)
        # 简单量化
        exhaust = (extreme_position > 0.8) & (momentum_ratio < 0.5)
        # 另外，价格创新高但动量弱也是衰竭
        new_high = close == rolling_high
        weak_momentum = (ret_short < ret_long * 0.5) & (ret_long > 0)
        exhaust2 = new_high & weak_momentum
        # 合并
        result = np.where(exhaust | exhaust2, -1.0, 0.0)
        # 也考虑正向情况：趋势健康（位置适中，动量强劲）
        healthy = (extreme_position < 0.3) & (momentum_ratio > 1.5)
        result = np.where(healthy, 1.0, result)
        result = pd.Series(result, index=data.index)
        # 填充前20期
        result.iloc[:20] = 0.0
        return result
