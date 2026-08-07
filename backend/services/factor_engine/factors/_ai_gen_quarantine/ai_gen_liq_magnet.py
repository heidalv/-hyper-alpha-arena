"""AI因子: 流动性磁铁反转 | 置信:60% | 捕捉价格先快速下跌后V型反转的流动性狩猎模式。当价格跌破近期低点后迅速反弹并收于开盘价之上，同时成交量激增，表明空头陷阱，因子值趋于+1；反之趋势延续时趋于-1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityMagnetReversal(BaseFactor):
    """捕捉价格先快速下跌后V型反转的流动性狩猎模式。当价格跌破近期低点后迅速反弹并收于开盘价之上，同时成交量激增，表明空头陷阱，因子值趋于+1；反之趋势延续时趋于-1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liq_magnet",
            name="liquidity_magnet_reversal",
            display_name="流动性磁铁反转",
            description="捕捉价格先快速下跌后V型反转的流动性狩猎模式。当价格跌破近期低点后迅速反弹并收于开盘价之上，同时成交量激增，表明空头陷阱，因子值趋于+1；反之趋势延续时趋于-1。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 参数
        n = 5
        # 日内最低点相对于前N日最低点
        prev_low = data['low'].rolling(n).min().shift(1)
        breach = (prev_low - data['low']) / (data['high'] - data['low'] + 1e-10)  # 向下突破深度
        # 反弹强度：从最低点到收盘的涨幅 / 日内振幅
        reversal = (data['close'] - data['low']) / (data['high'] - data['low'] + 1e-10)
        # 成交量放大
        avg_vol = data['volume'].rolling(n).mean().shift(1)
        vol_ratio = data['volume'] / (avg_vol + 1e-10)
        # 综合得分：突破深度 * 反弹强度 * 成交量比率，符号为反弹方向
        raw = breach * reversal * (vol_ratio - 1).clip(0, 5) * 2
        # 使用指数平滑
        result = raw.ewm(span=5, adjust=False).mean()
        result = result.clip(-1, 1)
        return result
