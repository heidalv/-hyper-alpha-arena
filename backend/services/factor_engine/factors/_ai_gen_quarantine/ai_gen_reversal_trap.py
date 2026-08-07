"""AI因子: RSI背离反转陷阱 | 置信:65% | 价格创20日新高但RSI未创新高（顶背离），表明上升动力减弱，易引发AI反转类亏损。计算过去20日最高价及同期RSI（14日）最高值，若价格创新高而RSI未创新高，则发出-1信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RSI_Divergence_Reversal_Trap(BaseFactor):
    """价格创20日新高但RSI未创新高（顶背离），表明上升动力减弱，易引发AI反转类亏损。计算过去20日最高价及同期RSI（14日）最高值，若价格创新高而RSI未创新高，则发出-1信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reversal_trap",
            name="RSI Divergence Reversal Trap",
            display_name="RSI背离反转陷阱",
            description="价格创20日新高但RSI未创新高（顶背离），表明上升动力减弱，易引发AI反转类亏损。计算过去20日最高价及同期RSI（14日）最高值，若价格创新高而RSI未创新高，则发出-1信号。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        # RSI计算
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        # 20日窗口检查
        window = 20
        # 当前价格是否为20日新高
        high_20 = high.rolling(window).max()
        price_new_high = (high == high_20) & (high_20.notna())
        # 当前RSI相对于20日内的最高RSI
        rsi_high_20 = rsi.rolling(window).max()
        rsi_not_new_high = (rsi < rsi_high_20) & (rsi_high_20.notna())
        # 同时满足价格新高且RSI未新高，则为顶背离
        divergence = price_new_high & rsi_not_new_high
        result = pd.Series(np.where(divergence, -1.0, 0.0), index=data.index)
        return result
