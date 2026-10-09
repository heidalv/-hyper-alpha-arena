"""AI因子: 做空超时风险 | 置信:70% | 针对做空单因max_hold_timeout亏损的模式，识别价格在持仓期间缓慢上行导致做空被套的情况。使用短期均线与长期均线的比值，结合价格相对开盘价的正向偏离，当价格持续高于短期均线且短期均线高于长期均线时，做空风险高，因子值偏向+1；反之偏向-1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortTimeoutRisk(BaseFactor):
    """针对做空单因max_hold_timeout亏损的模式，识别价格在持仓期间缓慢上行导致做空被套的情况。使用短期均线与长期均线的比值，结合价格相对开盘价的正向偏离，当价格持续高于短期均线且短期均线高于长期均线时，做空风险高，因子值偏向+1；反之偏向-1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_short_timeout_risk",
            name="Short Timeout Risk",
            display_name="做空超时风险",
            description="针对做空单因max_hold_timeout亏损的模式，识别价格在持仓期间缓慢上行导致做空被套的情况。使用短期均线与长期均线的比值，结合价格相对开盘价的正向偏离，当价格持续高于短期均线且短期均线高于长期均线时，做空风险高，因子值偏向+1；反之偏向-1。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_ma = data['close'].rolling(5).mean()
        long_ma = data['close'].rolling(20).mean()
        ma_ratio = short_ma / (long_ma + 1e-9)
        price_vs_open = (data['close'] - data['open']) / (data['open'] + 1e-9)
        result = (ma_ratio - 1.0) * 2 + price_vs_open
        result = result.clip(-1, 1)
        return result
