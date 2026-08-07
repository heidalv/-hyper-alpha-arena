"""AI因子: 区间停滞度 | 置信:65% | 识别价格在一段时间内窄幅震荡、缺乏方向的情况，容易导致持仓超时（hold_timeout）或止损频率增加。通过计算当前价格相对于近期波动范围的百分位和波动率压缩程度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RangeStagnation(BaseFactor):
    """识别价格在一段时间内窄幅震荡、缺乏方向的情况，容易导致持仓超时（hold_timeout）或止损频率增加。通过计算当前价格相对于近期波动范围的百分位和波动率压缩程度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rangestag",
            name="Range Stagnation",
            display_name="区间停滞度",
            description="识别价格在一段时间内窄幅震荡、缺乏方向的情况，容易导致持仓超时（hold_timeout）或止损频率增加。通过计算当前价格相对于近期波动范围的百分位和波动率压缩程度。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算过去20根K线的区间宽度
        max_high = high.rolling(20).max()
        min_low = low.rolling(20).min()
        range_width = (max_high - min_low) / close * 100  # 百分比
        # 计算当前价格在区间内的位置（0~1）
        position = (close - min_low) / (max_high - min_low + 1e-10)
        # 计算ATR与区间宽度的比值，衡量波动压缩程度
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr = tr.rolling(14).mean()
        compression = atr / (range_width * close / 100 + 1e-10)  # 接近0表示压缩
        # 停滞度：区间宽度小且位置靠近中间（无突破迹象）
        stagnation = np.exp(-range_width) * (1 - 2*abs(position - 0.5))
        # 结合压缩程度
        factor = stagnation * (1 - compression)
        # 归一化到[-1,1]
        result = (factor - 0.5) * 2  # 假设factor在0~1之间
        return result.fillna(0).clip(-1,1)
