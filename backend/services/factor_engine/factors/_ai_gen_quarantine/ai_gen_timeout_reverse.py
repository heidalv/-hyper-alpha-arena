"""AI因子: 超时反转信号 | 置信:60% | 专门捕捉因持仓时间过长导致趋势反转的场景，结合价格通道突破回测与成交量背离，正值表示应做空，负值表示应做多"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TimeoutReversalSignal(BaseFactor):
    """专门捕捉因持仓时间过长导致趋势反转的场景，结合价格通道突破回测与成交量背离，正值表示应做空，负值表示应做多"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeout_reverse",
            name="Timeout Reversal Signal",
            display_name="超时反转信号",
            description="专门捕捉因持仓时间过长导致趋势反转的场景，结合价格通道突破回测与成交量背离，正值表示应做空，负值表示应做多",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        df = data.copy()
        # 布林带参数
        bb_period = 20
        bb_std = 2
        ma = df['close'].rolling(bb_period).mean()
        std = df['close'].rolling(bb_period).std()
        upper_band = ma + bb_std * std
        lower_band = ma - bb_std * std
        # 价格突破上轨/下轨
        above_upper = df['close'] > upper_band
        below_lower = df['close'] < lower_band
        # 突破后成交量萎缩：确认动力不足
        vol_ma5 = df['volume'].rolling(5).mean()
        vol_ma20 = df['volume'].rolling(20).mean()
        vol_shrink = vol_ma5 < vol_ma20
        # RSI 14
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / (avg_loss + 1e-9)
        rsi = 100 - (100 / (1 + rs))
        # 背离：价格创新高而RSI未创新高，或价格创新低而RSI未创新低
        close_high_5 = df['close'].rolling(5).max()
        close_low_5 = df['close'].rolling(5).min()
        rsi_high_5 = rsi.rolling(5).max()
        rsi_low_5 = rsi.rolling(5).min()
        bearish_div = (df['close'] >= close_high_5) & (rsi < rsi_high_5)
        bullish_div = (df['close'] <= close_low_5) & (rsi > rsi_low_5)
        # 综合信号
        signal = np.where(above_upper & vol_shrink & bearish_div, 1.0,
                         np.where(below_lower & vol_shrink & bullish_div, -1.0, 0.0))
        # 平滑
        signal_smooth = pd.Series(signal).rolling(3).mean()
        result = signal_smooth.clip(-1, 1).fillna(0)
        return result
