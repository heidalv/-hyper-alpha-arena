"""AI因子: 窄幅震荡反转因子 | 置信:50% | 识别连续多个K线价格波动极小（实体和影线均窄），成交量萎缩，随后可能出现突破失败的反转。计算过去N根K线的高低价差相对ATR的比率，以及实体占比，当波动率压缩到极低水平时，方向与时量因子结合判断反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Tiny_Range_Reversal(BaseFactor):
    """识别连续多个K线价格波动极小（实体和影线均窄），成交量萎缩，随后可能出现突破失败的反转。计算过去N根K线的高低价差相对ATR的比率，以及实体占比，当波动率压缩到极低水平时，方向与时量因子结合判断反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tiny_range",
            name="Tiny_Range_Reversal",
            display_name="窄幅震荡反转因子",
            description="识别连续多个K线价格波动极小（实体和影线均窄），成交量萎缩，随后可能出现突破失败的反转。计算过去N根K线的高低价差相对ATR的比率，以及实体占比，当波动率压缩到极低水平时，方向与时量因子结合判断反转。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            import numpy as np
            import pandas as pd
            # 参数
            n = 5
            tiny_threshold = 0.3  # 高低差/ATR的阈值
            # 计算ATR
            high_low = data['high'] - data['low']
            atr = high_low.rolling(14).mean()
            # 计算最近n根K线的平均高低差占ATR比例
            range_ratio = high_low.rolling(n).mean() / atr
            # 计算收盘价变化方向（过去n根）
            ret_n = data['close'].pct_change(n)
            # 当范围极度缩小时，若之前上涨则预期回调，之前下跌则预期反弹
            tiny_condition = range_ratio < tiny_threshold
            # 用短期动量方向
            signal = pd.Series(0.0, index=data.index)
            signal[tiny_condition & (ret_n > 0)] = -0.8  # 上涨后缩量盘整，看跌
            signal[tiny_condition & (ret_n < 0)] = 0.8   # 下跌后缩量盘整，看涨
            # 去掉极端值
            signal = signal.clip(-1, 1)
            return signal
