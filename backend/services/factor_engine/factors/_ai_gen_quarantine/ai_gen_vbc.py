"""AI因子: 量价突破置信度 | 置信:70% | 评估价格突破近期高低点时是否有成交量配合。无量的突破往往演变为假突破并导致反转亏损。因子值+1表示放量有效突破（顺势安全），-1表示无量突破或假突破风险极高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeBackedBreakoutConfidence(BaseFactor):
    """评估价格突破近期高低点时是否有成交量配合。无量的突破往往演变为假突破并导致反转亏损。因子值+1表示放量有效突破（顺势安全），-1表示无量突破或假突破风险极高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vbc",
            name="Volume-Backed Breakout Confidence",
            display_name="量价突破置信度",
            description="评估价格突破近期高低点时是否有成交量配合。无量的突破往往演变为假突破并导致反转亏损。因子值+1表示放量有效突破（顺势安全），-1表示无量突破或假突破风险极高。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        high = df['high']
        low = df['low']
        close = df['close']
        volume = df['volume']
        # 20期最高价和最低价
        high_20 = high.rolling(20).max()
        low_20 = low.rolling(20).min()
        # 突破向上：收盘价高于前20日最高
        breakout_up = close > high_20.shift(1)
        # 突破向下：收盘价低于前20日最低
        breakout_down = close < low_20.shift(1)
        # 成交量确认：当前成交量是否大于过去20日均量的1.5倍
        vol_ma20 = volume.rolling(20).mean()
        vol_confirm = volume > 1.5 * vol_ma20
        # 无量突破
        weak_up = breakout_up & ~vol_confirm
        weak_down = breakout_down & ~vol_confirm
        strong_up = breakout_up & vol_confirm
        strong_down = breakout_down & vol_confirm
        # 构建信号强度
        signal = pd.Series(0, index=df.index, dtype=float)
        signal[strong_up] = 1
        signal[strong_down] = -1
        signal[weak_up] = -0.7
        signal[weak_down] = 0.7
        # 为了连续化，用突破强度 * 量比
        # 量比
        vol_ratio = volume / vol_ma20.replace(0, 1e-9)
        vol_ratio_clip = vol_ratio.clip(0.5, 3)
        # 突破幅度：收盘价超出边界的百分比
        up_distance = (close - high_20.shift(1)) / high_20.shift(1)
        down_distance = (low_20.shift(1) - close) / low_20.shift(1)
        up_distance = up_distance.clip(0, 0.1) * 10  # 10% max
        down_distance = down_distance.clip(0, 0.1) * 10
        # 突破置信度 = 方向 * 距离 * 量比归一化
        raw = (up_distance - down_distance) * (vol_ratio_clip - 1) / 2
        raw = raw.fillna(0).clip(-1, 1)
        return raw
