"""AI因子: 微利反转风险 | 置信:60% | 检测市场频繁出现小幅上涨后迅速回落的模式，预示缺乏持续买盘，容易导致微利了结后反转或止损。因子值-1表示微利反转风险高，+1表示上涨具有持续性。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MicroProfitReversalRisk(BaseFactor):
    """检测市场频繁出现小幅上涨后迅速回落的模式，预示缺乏持续买盘，容易导致微利了结后反转或止损。因子值-1表示微利反转风险高，+1表示上涨具有持续性。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mini_rev",
            name="Micro Profit Reversal Risk",
            display_name="微利反转风险",
            description="检测市场频繁出现小幅上涨后迅速回落的模式，预示缺乏持续买盘，容易导致微利了结后反转或止损。因子值-1表示微利反转风险高，+1表示上涨具有持续性。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close'].astype(float)
        open_ = data['open'].astype(float)
        high = data['high'].astype(float)
        low = data['low'].astype(float)
        # 1. 上影线占比
        body = np.abs(close - open_)
        upper_wick = high - np.maximum(open_, close)
        wick_ratio = upper_wick / (high - low + 1e-9)
        # 2. 小幅上涨后下一根反转
        tiny_up = (close > open_) & (body / (open_ + 1e-9) < 0.005)  # 涨幅<0.5%
        next_reversal = (close.shift(-1) < open_.shift(-1)) & (close.shift(-1) < close)
        reversal_count = (tiny_up & next_reversal).rolling(10).sum()
        # 3. 近期高点受阻
        high_20 = high.rolling(20).max()
        near_high = (high >= high_20 * 0.98)
        fail_break = near_high & (close < open_) & (close < high * 0.995)
        fail_count = fail_break.rolling(10).sum()
        # 综合风险：高上影线+频繁反转+突破失败
        risk = (wick_ratio.rolling(5).mean() + reversal_count/10.0 + fail_count/10.0) / 3
        # 映射：高风险为-1，低风险为+1
        result = pd.Series(1 - 2*risk, index=data.index).clip(-1, 1).fillna(0)
        return result
