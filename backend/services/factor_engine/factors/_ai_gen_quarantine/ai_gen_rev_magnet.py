"""AI因子: 反转磁力指标 | 置信:65% | 通过价格高位反转和成交量异常放大后萎缩识别流动性吸引子反转模式，适用于regime=unknown状态。当价格接近近期高点且成交量先放大后急剧萎缩，同时价格小幅回落，预示反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReverseMagnetIndicator(BaseFactor):
    """通过价格高位反转和成交量异常放大后萎缩识别流动性吸引子反转模式，适用于regime=unknown状态。当价格接近近期高点且成交量先放大后急剧萎缩，同时价格小幅回落，预示反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rev_magnet",
            name="Reverse Magnet Indicator",
            display_name="反转磁力指标",
            description="通过价格高位反转和成交量异常放大后萎缩识别流动性吸引子反转模式，适用于regime=unknown状态。当价格接近近期高点且成交量先放大后急剧萎缩，同时价格小幅回落，预示反转。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        close = data['close']
        volume = data['volume']
        # 计算过去20日最高价
        rolling_high = high.rolling(20).max()
        # 价格相对高位比例: (close - rolling_high) / (rolling_high - rolling_low) 近似用close/rolling_high
        price_ratio = close / rolling_high  # 接近1表示高位
        # 成交量变化: 过去5日均量
        vol_ma5 = volume.rolling(5).mean()
        vol_ma20 = volume.rolling(20).mean()
        # 成交量异常: 当前量比20日均量高但比5日均量低(萎缩)
        vol_surge = (volume > vol_ma20 * 1.5) & (volume < vol_ma5 * 0.8)
        # 价格小幅回落: 当日涨幅小于0.5%且前一日涨幅为正
        pct_change = close.pct_change()
        price_dip = (pct_change < 0.005) & (pct_change.shift(1) > 0)
        # 结合: 高位 + 成交量异常 + 价格小幅回落
        signal = (price_ratio > 0.98) & vol_surge & price_dip
        result = signal.astype(float) * 2 - 1  # 转为-1~1
        # 平滑处理
        result = result.rolling(3, min_periods=1).mean()
        return result.fillna(0)
