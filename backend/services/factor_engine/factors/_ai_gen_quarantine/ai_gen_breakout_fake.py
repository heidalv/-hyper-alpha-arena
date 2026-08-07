"""AI因子: 假突破识别因子 | 置信:60% | 检测价格在关键均线附近反复穿越且成交量未能有效确认的假突破行为。当价格突破均线但成交量低于近期均值时，视为假突破，因子输出负值，提示做多/做空风险；反之真突破输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class FakeBreakoutDetector(BaseFactor):
    """检测价格在关键均线附近反复穿越且成交量未能有效确认的假突破行为。当价格突破均线但成交量低于近期均值时，视为假突破，因子输出负值，提示做多/做空风险；反之真突破输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_breakout_fake",
            name="Fake Breakout Detector",
            display_name="假突破识别因子",
            description="检测价格在关键均线附近反复穿越且成交量未能有效确认的假突破行为。当价格突破均线但成交量低于近期均值时，视为假突破，因子输出负值，提示做多/做空风险；反之真突破输出正值。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        # 计算20日均线
        ma20 = close.rolling(20).mean()
        # 计算20日均成交量
        vol_ma20 = volume.rolling(20).mean()
        # 价格与均线距离百分比
        dist = (close - ma20) / ma20
        # 突破判定：价格穿越均线（dist符号变化）且距离超过0.5%
        cross_up = (dist > 0.005) & (dist.shift(1) <= 0.005)
        cross_down = (dist < -0.005) & (dist.shift(1) >= -0.005)
        # 成交量确认：当前成交量高于均量1.5倍为真突破
        vol_confirm = volume > vol_ma20 * 1.5
        # 假突破：穿越但成交量不足
        fake_up = cross_up & (~vol_confirm)
        fake_down = cross_down & (~vol_confirm)
        real_up = cross_up & vol_confirm
        real_down = cross_down & vol_confirm
        # 合成信号：假突破为负，真突破为正，其余为0
        signal = pd.Series(0.0, index=close.index)
        signal[fake_up | fake_down] = -1.0
        signal[real_up | real_down] = 1.0
        return signal
