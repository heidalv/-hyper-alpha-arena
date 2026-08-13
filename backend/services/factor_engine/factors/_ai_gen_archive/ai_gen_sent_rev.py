"""AI因子: 极端情绪反转 | 置信:62% | 基于日内价格极端波动与成交量突增识别情绪反转：计算(最高价-最低价)/收盘价作为波动幅度，乘以成交量相对20日均值的倍数，再结合收盘价在当日区间内的相对位置（(收盘-最低)/(最高-最低)）。当波动极大且收盘靠近最低时（恐慌性止损），因子接近-1；反之极端上涨且收盘靠近最高时，因子接近+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class SentimentReversal(BaseFactor):
    """基于日内价格极端波动与成交量突增识别情绪反转：计算(最高价-最低价)/收盘价作为波动幅度，乘以成交量相对20日均值的倍数，再结合收盘价在当日区间内的相对位置（(收盘-最低)/(最高-最低)）。当波动极大且收盘靠近最低时（恐慌性止损），因子接近-1；反之极端上涨且收盘靠近最高时，因子接近+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sent_rev",
            name="SentimentReversal",
            display_name="极端情绪反转",
            description="基于日内价格极端波动与成交量突增识别情绪反转：计算(最高价-最低价)/收盘价作为波动幅度，乘以成交量相对20日均值的倍数，再结合收盘价在当日区间内的相对位置（(收盘-最低)/(最高-最低)）。当波动极大且收盘靠近最低时（恐慌性止损），因子接近-1；反之极端上涨且收盘靠近最高时，因子接近+1。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        # 日内波动幅度
        range_pct = (high - low) / close
        # 成交量倍数
        vol20 = volume.rolling(20).mean()
        vol_mult = volume / (vol20 + 1e-8)
        # 收盘位置（0到1）
        pos = (close - low) / (high - low + 1e-8)
        # 极端信号：波动大 + 成交量放大 + 位置极端
        raw = (pos - 0.5) * 2 * range_pct * vol_mult
        # 归一化
        result = np.tanh(raw * 10)
        return result
