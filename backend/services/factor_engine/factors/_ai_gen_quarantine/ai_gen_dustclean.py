"""AI因子: 尘埃清理模式 | 置信:55% | 检测在极低成交量下出现的异常价格波动，通常为庄家清理小单（dust cleanup）导致的小幅反转。当成交量低于过去20日均量的20%，且价格变化幅度超过过去20日平均振幅的2倍时，认为存在清理行为，值接近+1；否则接近-1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class DustCleanupPattern(BaseFactor):
    """检测在极低成交量下出现的异常价格波动，通常为庄家清理小单（dust cleanup）导致的小幅反转。当成交量低于过去20日均量的20%，且价格变化幅度超过过去20日平均振幅的2倍时，认为存在清理行为，值接近+1；否则接近-1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_dustclean",
            name="Dust Cleanup Pattern",
            display_name="尘埃清理模式",
            description="检测在极低成交量下出现的异常价格波动，通常为庄家清理小单（dust cleanup）导致的小幅反转。当成交量低于过去20日均量的20%，且价格变化幅度超过过去20日平均振幅的2倍时，认为存在清理行为，值接近+1；否则接近-1。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        period = 20
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        # 价格变化幅度（当前K线振幅）
        candle_range = high - low
        avg_range = candle_range.rolling(window=period, min_periods=period).mean()
        # 成交量均值和低量阈值
        vol_ma = volume.rolling(window=period, min_periods=period).mean()
        low_vol_thresh = 0.2 * vol_ma
        # 低成交量且振幅异常大
        condition_volume = volume < low_vol_thresh
        condition_range = candle_range > 2 * avg_range
        # 同时还要考虑价格方向？通常dust_cleanup可能伴随小幅度反转，但这里只检测模式
        signal = pd.Series(np.where(condition_volume & condition_range, 1, -1), index=data.index)
        # 平滑
        signal = signal.rolling(2, min_periods=1).mean()
        return signal.fillna(-1).clip(-1, 1)
