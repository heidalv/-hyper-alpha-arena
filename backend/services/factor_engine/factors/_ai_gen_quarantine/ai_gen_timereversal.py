"""AI因子: 时间反转因子 | 置信:55% | 基于持仓超时（max_hold_timeout）亏损模式，检测价格在一定时间内未能突破关键水平而可能反转的形态。计算过去N根K线内价格在布林带中的位置变化率，当价格持续在中轨附近震荡且波动率收缩时，做多风险增加。这里N=10。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TimeBasedReversalFactor(BaseFactor):
    """基于持仓超时（max_hold_timeout）亏损模式，检测价格在一定时间内未能突破关键水平而可能反转的形态。计算过去N根K线内价格在布林带中的位置变化率，当价格持续在中轨附近震荡且波动率收缩时，做多风险增加。这里N=10。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timereversal",
            name="Time-Based Reversal Factor",
            display_name="时间反转因子",
            description="基于持仓超时（max_hold_timeout）亏损模式，检测价格在一定时间内未能突破关键水平而可能反转的形态。计算过去N根K线内价格在布林带中的位置变化率，当价格持续在中轨附近震荡且波动率收缩时，做多风险增加。这里N=10。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper = ma20 + 2 * std20
        lower = ma20 - 2 * std20
        pos = (close - lower) / (upper - lower + 1e-8)
        pos_chg = pos.diff(10)
        vol_ratio = (high.rolling(10).max() - low.rolling(10).min()) / (close.rolling(10).mean() + 1e-8)
        raw = -pos_chg * (1 - vol_ratio)
        result = raw.rank(pct=True) * 2 - 1
        return result.fillna(0)
