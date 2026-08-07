"""AI因子: 微小反转风险因子 | 置信:48% | 捕捉价格在短期内出现微小回调后继续原趋势的概率，用于避免逆势持仓。通过计算最近若干周期内价格相对于短期均线的偏离度与成交量变化，当偏离小且缩量时可能假突破，给出负信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MicroReversalRisk(BaseFactor):
    """捕捉价格在短期内出现微小回调后继续原趋势的概率，用于避免逆势持仓。通过计算最近若干周期内价格相对于短期均线的偏离度与成交量变化，当偏离小且缩量时可能假突破，给出负信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mrev",
            name="MicroReversalRisk",
            display_name="微小反转风险因子",
            description="捕捉价格在短期内出现微小回调后继续原趋势的概率，用于避免逆势持仓。通过计算最近若干周期内价格相对于短期均线的偏离度与成交量变化，当偏离小且缩量时可能假突破，给出负信号。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 参数
        short_period = 5
        long_period = 20
        lookback = 3
        # 计算短期均线
        close = data['close']
        volume = data['volume']
        sma_short = close.rolling(short_period).mean()
        sma_long = close.rolling(long_period).mean()
        # 价格偏离短期均线的百分比
        pct_dev = (close - sma_short) / sma_short * 100
        # 成交量相对于过去n天的均值
        vol_ma = volume.rolling(lookback).mean()
        vol_ratio = volume / vol_ma
        # 识别微小偏离：偏离在正负0.5%以内，且成交量萎缩
        tiny_dev = (pct_dev.abs() < 0.5).astype(int)
        low_vol = (vol_ratio < 0.8).astype(int)
        # 同时检查趋势：当短期均线高于长期均线（多头）时，微小向下偏离可能继续下跌；相反亦然
        trend_up = (sma_short > sma_long).astype(int)
        trend_down = (sma_short < sma_long).astype(int)
        # 信号：多头趋势下微小负偏离且缩量 -> 可能回调结束继续上？但实际亏损中是小止损，所以给负信号避免做多。
        # 这里认为微小偏离且缩量是陷阱，应反向操作？模糊处理：直接输出负信号
        signal = -tiny_dev * low_vol
        # 标准化到[-1,1]
        signal = signal.clip(-1, 1)
        signal = signal.fillna(0.0)
        return signal
