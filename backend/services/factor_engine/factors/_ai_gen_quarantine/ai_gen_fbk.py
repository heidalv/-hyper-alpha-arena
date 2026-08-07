"""AI因子: 假突破强度 | 置信:60% | 检测价格突破近期高/低点后是否迅速反转。计算当前收盘价相对最近N日最高价/最低价的偏离，并结合ATR归一化，当突破后回撤超过阈值时给出负信号。值域[-1,1]，负值表示假突破风险高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Fake_Breakout_Intensity(BaseFactor):
    """检测价格突破近期高/低点后是否迅速反转。计算当前收盘价相对最近N日最高价/最低价的偏离，并结合ATR归一化，当突破后回撤超过阈值时给出负信号。值域[-1,1]，负值表示假突破风险高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_fbk",
            name="Fake Breakout Intensity",
            display_name="假突破强度",
            description="检测价格突破近期高/低点后是否迅速反转。计算当前收盘价相对最近N日最高价/最低价的偏离，并结合ATR归一化，当突破后回撤超过阈值时给出负信号。值域[-1,1]，负值表示假突破风险高。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        n = 20
        high = data['high']
        low = data['low']
        close = data['close']
        # 最高价和最低价滚动
        recent_high = high.rolling(n).max()
        recent_low = low.rolling(n).min()
        # ATR
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(n).mean()
        atr += 1e-10
        # 突破信号：价格超过最近高点或跌破最近低点
        break_high = close > recent_high.shift(1)
        break_low = close < recent_low.shift(1)
        # 计算突破后回撤幅度 (当前收盘相对突破点的距离)
        retrace_high = (close - recent_high.shift(1)) / atr
        retrace_low = (recent_low.shift(1) - close) / atr
        # 假突破：突破后回撤很快（用当前收盘位置相对突破点，如果突破高点后收盘低于高点，则为负)
        fake_high = break_high & (retrace_high < -0.5)  # 回撤超过0.5ATR
        fake_low = break_low & (retrace_low < -0.5)
        # 综合信号：假突破时给负分，否则中性
        signal = pd.Series(0.0, index=data.index)
        signal[fake_high] = -1.0
        signal[fake_low] = -1.0
        # 平滑处理：取累计均值或保持
        # 为增强稳定性，使用滚动平均
        result = signal.rolling(5).mean().fillna(0.0)
        return result
