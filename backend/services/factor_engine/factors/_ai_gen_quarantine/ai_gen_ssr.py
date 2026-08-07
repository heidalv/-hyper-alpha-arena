"""AI因子: 止损滑点风险 | 置信:50% | 捕捉价格突破近期极值后快速反转的风险，这种模式容易导致止损触发。通过计算当前价格相对于过去N周期最高最低的位置以及短期反转强度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class StopSlipRisk(BaseFactor):
    """捕捉价格突破近期极值后快速反转的风险，这种模式容易导致止损触发。通过计算当前价格相对于过去N周期最高最低的位置以及短期反转强度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ssr",
            name="StopSlipRisk",
            display_name="止损滑点风险",
            description="捕捉价格突破近期极值后快速反转的风险，这种模式容易导致止损触发。通过计算当前价格相对于过去N周期最高最低的位置以及短期反转强度。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        # 过去10周期最高价和最低价
        highest = high.rolling(10).max()
        lowest = low.rolling(10).min()
        # 价格在区间内的位置
        pos = (close - lowest) / (highest - lowest + 1e-8)
        # 计算短期价格反转强度：最近1周期变化与过去3周期平均变化的背离
        ret1 = close.pct_change(1)
        ret3_avg = close.pct_change(3).rolling(3).mean()
        reversal = ret1 - ret3_avg
        # 当价格接近极值且反转信号强时，止损风险高
        raw = -pos * reversal
        norm = (raw - raw.rolling(20).mean()) / (raw.rolling(20).std() + 1e-8)
        return norm.clip(-1, 1)
