"""AI因子: 动量衰竭反转因子 | 置信:60% | 检测短期动量过度延伸后的反转信号。计算N日（如5日）价格变化率和N日ATR，当价格变动超过3倍ATR且随后出现价格回调（如最新收盘价低于前一日高点）时，认为动量衰竭，可能反转。适合捕捉无序震荡中的小趋势结束。因子返回做多（+1）或做空（-1）基于前一波方向的反向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumExhaustionReversal(BaseFactor):
    """检测短期动量过度延伸后的反转信号。计算N日（如5日）价格变化率和N日ATR，当价格变动超过3倍ATR且随后出现价格回调（如最新收盘价低于前一日高点）时，认为动量衰竭，可能反转。适合捕捉无序震荡中的小趋势结束。因子返回做多（+1）或做空（-1）基于前一波方向的反向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_exhaustion",
            name="Momentum Exhaustion Reversal",
            display_name="动量衰竭反转因子",
            description="检测短期动量过度延伸后的反转信号。计算N日（如5日）价格变化率和N日ATR，当价格变动超过3倍ATR且随后出现价格回调（如最新收盘价低于前一日高点）时，认为动量衰竭，可能反转。适合捕捉无序震荡中的小趋势结束。因子返回做多（+1）或做空（-1）基于前一波方向的反向。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        high = data['high']
        low = data['low']
    
        # 5日ATR
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr5 = tr.rolling(5).mean()
    
        # 5日价格变动
        delta5 = close - close.shift(5)
    
        # 3倍ATR条件
        cond_momentum = abs(delta5) > 3 * atr5
    
        # 并且当日价格回撤：对于上涨趋势（delta5>0），要求close低于前一日的high；对于下跌趋势，要求close高于前一日的low
        cond_reversal = np.where(
            delta5 > 0,
            close < high.shift(1),
            close > low.shift(1)
        )
    
        cond = cond_momentum & cond_reversal
    
        # 方向：反向，即之前涨则做空（-1），之前跌则做多（+1）
        direction = np.where(delta5 > 0, -1, 1)
        result = pd.Series(np.where(cond, direction, 0), index=data.index)
        return result
