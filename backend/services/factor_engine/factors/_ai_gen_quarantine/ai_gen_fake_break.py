"""AI因子: 虚假突破检测器 | 置信:60% | 检测价格突破近期高点/低点但成交量没有显著放大（或成交量萎缩），且随后价格回撤到突破前的区间内，判断为假突破，因子输出负信号避免追入。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class FakeBreakoutDetector(BaseFactor):
    """检测价格突破近期高点/低点但成交量没有显著放大（或成交量萎缩），且随后价格回撤到突破前的区间内，判断为假突破，因子输出负信号避免追入。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_fake_break",
            name="fake_breakout_detector",
            display_name="虚假突破检测器",
            description="检测价格突破近期高点/低点但成交量没有显著放大（或成交量萎缩），且随后价格回撤到突破前的区间内，判断为假突破，因子输出负信号避免追入。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: pd.DataFrame with columns ['open','high','low','close','volume']
        import numpy as np
        import pandas as pd
        # 参数
        lookback = 10        # 近期极值窗口
        retrace_ratio = 0.5  # 回撤阈值（突破幅度的50%）
        vol_mult = 1.2       # 成交量放大倍数阈值

        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']

        # 计算过去N日最高价和最低价
        max_high = high.rolling(lookback, min_periods=1).max()
        min_low = low.rolling(lookback, min_periods=1).min()

        # 当前价格距前期高点的比例
        breakout_up = (close >= max_high.shift(1)) & (close > close.shift(1))  # 上突破
        breakout_down = (close <= min_low.shift(1)) & (close < close.shift(1)) # 下突破

        # 突破时的成交量对比：当前量 vs 前一期平均量
        avg_vol = volume.rolling(5).mean()
        vol_increase = volume > avg_vol * vol_mult

        # 检测假突破条件：突破但成交量未放大
        fake_up = breakout_up & ~vol_increase
        fake_down = breakout_down & ~vol_increase

        # 进一步验证后一根K线是否回撤超过突破幅度的50%
        # 上突破后，下一根K线低点低于 (突破高点 + 突破后回撤幅度)
        # 简化：使用当前突破信号，后续条件由滞后判断
        # 这里采用当前信号结合前一根突破状态：若上一根假突破且当前回撤
        prev_fake_up = fake_up.shift(1)
        prev_fake_down = fake_down.shift(1)
        # 回撤幅度计算：突破当日的收盘价与后一日价格比较
        retrace_up = prev_fake_up & (close < high.shift(1) - (high.shift(1) - close.shift(1)) * retrace_ratio)
        retrace_down = prev_fake_down & (close > low.shift(1) + (close.shift(1) - low.shift(1)) * retrace_ratio)

        # 综合信号：当前出现假突破或刚确认回撤时给出负向
        signal = (fake_up | fake_down | retrace_up | retrace_down).astype(float)
        # 将信号映射到[-1, 0]
        result = -signal.clip(0, 1) * 0.8
        return result
