"""AI因子: RSI极端反转 | 置信:60% | 基于14周期RSI指标，当RSI低于30（超卖）时输出正值（看多），当RSI高于70（超买）时输出负值（看空），中间区域输出0。使用线性映射使值连续变化，捕捉均值回复机会。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RSIExtremeReversal(BaseFactor):
    """基于14周期RSI指标，当RSI低于30（超卖）时输出正值（看多），当RSI高于70（超买）时输出负值（看空），中间区域输出0。使用线性映射使值连续变化，捕捉均值回复机会。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rsi_extreme",
            name="RSI Extreme Reversal",
            display_name="RSI极端反转",
            description="基于14周期RSI指标，当RSI低于30（超卖）时输出正值（看多），当RSI高于70（超买）时输出负值（看空），中间区域输出0。使用线性映射使值连续变化，捕捉均值回复机会。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.rolling(14, min_periods=14).mean()
        avg_loss = loss.rolling(14, min_periods=14).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        # 映射：<30时线性从0到1（30对应0, 0对应1），>70时线性从0到-1（70对应0, 100对应-1）
        result = pd.Series(np.zeros_like(rsi), index=rsi.index)
        # 超卖区域
        oversold = rsi < 30
        result[oversold] = (30 - rsi[oversold]) / 30.0  # 0~1
        # 超买区域
        overbought = rsi > 70
        result[overbought] = (70 - rsi[overbought]) / 30.0  # 0~-1
        return result.fillna(0)
