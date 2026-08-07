"""AI因子: 连续止损频率 | 置信:60% | 模拟过去N根K线中微小亏损（-0.3%~-1%）出现的频率，结合当前价格相对于短期均线的偏离，当频繁出现类似小止损时发出负向警告。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ConsecutiveStopLossFrequency(BaseFactor):
    """模拟过去N根K线中微小亏损（-0.3%~-1%）出现的频率，结合当前价格相对于短期均线的偏离，当频繁出现类似小止损时发出负向警告。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_stop_freq",
            name="Consecutive Stop-Loss Frequency",
            display_name="连续止损频率",
            description="模拟过去N根K线中微小亏损（-0.3%~-1%）出现的频率，结合当前价格相对于短期均线的偏离，当频繁出现类似小止损时发出负向警告。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as n
        # 模拟止损信号: 当收盘价相对于前一根K线最高/最低出现反向小幅度
        # 假设做多止损: close < prev_low * 0.997 (约-0.3%)
        # 做空止损: close > prev_high * 1.003
        prev_high = data['high'].shift(1)
        prev_low = data['low'].shift(1)
        loss_long = (data['close'] < prev_low * 0.997).astype(int)
        loss_short = (data['close'] > prev_high * 1.003).astype(int)
        loss_total = loss_long + loss_short
        # 滚动求和过去10个周期的频率
        freq = loss_total.rolling(10).sum() / 10.0
        # 当前价格相对20日均线偏离
        ma20 = data['close'].rolling(20).mean()
        deviation = (data['close'] - ma20) / ma20
        # 当频繁止损且价格偏离均值时，可能延续反向
        signal = -freq * deviation.clip(-0.1, 0.1) * 10
        return signal.clip(-1, 1).fillna(0)
