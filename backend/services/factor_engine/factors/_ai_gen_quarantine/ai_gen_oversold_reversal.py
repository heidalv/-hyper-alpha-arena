"""AI因子: 超卖反转陷阱 | 置信:50% | 识别RSI进入超卖区域（<30）后快速反弹回30以上，并伴随成交量萎缩，暗示空头陷阱，此时做空易亏损。因子输出负值表示避免做空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class OversoldReversalTrapping(BaseFactor):
    """识别RSI进入超卖区域（<30）后快速反弹回30以上，并伴随成交量萎缩，暗示空头陷阱，此时做空易亏损。因子输出负值表示避免做空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_oversold_reversal",
            name="Oversold Reversal Trapping",
            display_name="超卖反转陷阱",
            description="识别RSI进入超卖区域（<30）后快速反弹回30以上，并伴随成交量萎缩，暗示空头陷阱，此时做空易亏损。因子输出负值表示避免做空。",
            category="sentiment",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
    
        # RSI计算
        delta = data['close'].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(window=14).mean()
        avg_loss = loss.rolling(window=14).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
    
        # 条件：前一根RSI小于30，当前RSI大于30，且成交量低于20日均量
        prev_rsi = rsi.shift(1)
        vol_avg = data['volume'].rolling(window=20).mean()
        cond_rsi_cross = (prev_rsi < 30) & (rsi >= 30)
        cond_volume = data['volume'] < vol_avg * 0.8
    
        signal = np.where(cond_rsi_cross & cond_volume, -1.0, 0.0)
        result = pd.Series(signal, index=data.index).fillna(0.0)
        return result
