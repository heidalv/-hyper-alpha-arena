"""AI因子: 假突破识别因子 | 置信:65% | 检测价格突破近期极值但成交量萎缩的形态，识别容易导致止损的假突破。当突破时成交量低于20日均量且后续价格回撤时输出负值，反之为正。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class FakeBreakoutDetector(BaseFactor):
    """检测价格突破近期极值但成交量萎缩的形态，识别容易导致止损的假突破。当突破时成交量低于20日均量且后续价格回撤时输出负值，反之为正。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_fakebreak",
            name="Fake Breakout Detector",
            display_name="假突破识别因子",
            description="检测价格突破近期极值但成交量萎缩的形态，识别容易导致止损的假突破。当突破时成交量低于20日均量且后续价格回撤时输出负值，反之为正。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high, low, close, vol = data['high'], data['low'], data['close'], data['volume']
        # 近期高点低点
        n = 20
        recent_high = high.rolling(n, min_periods=1).max()
        recent_low = low.rolling(n, min_periods=1).min()
        # 成交量均线
        vol_ma = vol.rolling(20).mean()
        # 突破信号：收盘价突破高点且成交量萎缩
        breakout_up = (close > recent_high.shift()) & (vol < vol_ma * 0.8)
        breakout_down = (close < recent_low.shift()) & (vol < vol_ma * 0.8)
        # 后续确认：假突破定义为随后3根K线回到区间内
        future_close = close.shift(-3)
        fake_up = breakout_up & (future_close < recent_high.shift())
        fake_down = breakout_down & (future_close > recent_low.shift())
        # 信号：假突破给出负向（-1），真实突破给出正向（+1），其余0
        signal = pd.Series(0, index=data.index)
        signal[fake_up] = -1.0
        signal[fake_down] = -1.0
        # 真实突破（量价配合）作为正向
        real_up = (close > recent_high.shift()) & (vol > vol_ma * 1.2) & (future_close > recent_high.shift())
        real_down = (close < recent_low.shift()) & (vol > vol_ma * 1.2) & (future_close < recent_low.shift())
        signal[real_up] = 1.0
        signal[real_down] = 1.0
        # 平滑处理：滚动求和后用tanh映射到-1~1
        signal_sum = signal.rolling(5).sum() / 3.0
        result = pd.Series(np.clip(signal_sum, -1, 1), index=data.index).fillna(0)
        return result
