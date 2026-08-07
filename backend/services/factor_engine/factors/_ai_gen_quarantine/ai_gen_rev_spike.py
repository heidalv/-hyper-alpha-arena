"""AI因子: 反转尖峰因子 | 置信:60% | 检测短期价格快速冲高或杀跌后成交量异常放大，随后出现反转信号。计算过去N根K线的价格变化率与成交量变化率的比值，当价格变化率超过阈值且成交量激增时，认为趋势衰竭，预期反向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Reversal_Spike(BaseFactor):
    """检测短期价格快速冲高或杀跌后成交量异常放大，随后出现反转信号。计算过去N根K线的价格变化率与成交量变化率的比值，当价格变化率超过阈值且成交量激增时，认为趋势衰竭，预期反向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rev_spike",
            name="Reversal_Spike",
            display_name="反转尖峰因子",
            description="检测短期价格快速冲高或杀跌后成交量异常放大，随后出现反转信号。计算过去N根K线的价格变化率与成交量变化率的比值，当价格变化率超过阈值且成交量激增时，认为趋势衰竭，预期反向。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            import numpy as np
            import pandas as pd
            # 参数
            lookback = 5
            vol_threshold = 2.0
            price_threshold = 0.02
            # 计算价格变化率
            returns = data['close'].pct_change(lookback)
            # 计算成交量变化率（相对过去N根均值）
            vol_ma = data['volume'].rolling(lookback).mean()
            vol_ratio = data['volume'] / vol_ma
            # 识别极端价格变化与成交量放大
            spike_up = (returns > price_threshold) & (vol_ratio > vol_threshold)
            spike_down = (returns < -price_threshold) & (vol_ratio > vol_threshold)
            # 信号：尖峰后预期反转，即上涨尖峰后看跌，下跌尖峰后看涨
            signal = pd.Series(0.0, index=data.index)
            signal[spike_up] = -1.0
            signal[spike_down] = 1.0
            # 平滑或延迟？直接返回当前信号
            return signal
