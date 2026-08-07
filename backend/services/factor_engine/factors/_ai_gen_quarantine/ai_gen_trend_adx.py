"""AI因子: 趋势强度反转因子 | 置信:60% | 基于ADX（平均趋向指数）判断市场趋势强度，当ADX低于阈值（20）且价格处于均线附近时，认为市场处于无趋势震荡状态，容易触发止损，此时因子偏向反向交易（做多或做空取决于价格偏离方向）。具体逻辑：计算14日ATR和ADX，当ADX<20且收盘价与20日均线偏离度小于1%时，返回-1（看空）或+1（看多）？实际上根据价格相对于均线位置：若在均线上方则倾向于回落（空头），反之则反弹（多头）。该因子旨在捕捉类似un known regime下的震荡行情。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendStrengthReversal(BaseFactor):
    """基于ADX（平均趋向指数）判断市场趋势强度，当ADX低于阈值（20）且价格处于均线附近时，认为市场处于无趋势震荡状态，容易触发止损，此时因子偏向反向交易（做多或做空取决于价格偏离方向）。具体逻辑：计算14日ATR和ADX，当ADX<20且收盘价与20日均线偏离度小于1%时，返回-1（看空）或+1（看多）？实际上根据价格相对于均线位置：若在均线上方则倾向于回落（空头），反之则反弹（多头）。该因子旨在捕捉类似un known regime下的震荡行情。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_adx",
            name="Trend Strength Reversal",
            display_name="趋势强度反转因子",
            description="基于ADX（平均趋向指数）判断市场趋势强度，当ADX低于阈值（20）且价格处于均线附近时，认为市场处于无趋势震荡状态，容易触发止损，此时因子偏向反向交易（做多或做空取决于价格偏离方向）。具体逻辑：计算14日ATR和ADX，当ADX<20且收盘价与20日均线偏离度小于1%时，返回-1（看空）或+1（看多）？实际上根据价格相对于均线位置：若在均线上方则倾向于回落（空头），反之则反弹（多头）。该因子旨在捕捉类似un known regime下的震荡行情。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        high = data['high']
        low = data['low']
    
        # 计算ATR
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr = tr.rolling(14).mean()
    
        # 计算ADX
        up = high - high.shift(1)
        down = low.shift(1) - low
        plus_dm = np.where((up > down) & (up > 0), up, 0)
        minus_dm = np.where((down > up) & (down > 0), down, 0)
        plus_di = 100 * pd.Series(plus_dm).rolling(14).mean() / atr
        minus_di = 100 * pd.Series(minus_dm).rolling(14).mean() / atr
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(14).mean()
    
        # 计算20日均线及偏离度
        ma20 = close.rolling(20).mean()
        deviation = (close - ma20) / ma20
    
        # 条件：ADX<20 且偏离度绝对值<0.01
        cond = (adx < 20) & (abs(deviation) < 0.01)
        # 生成信号：若价格高于均线则看空（-1），低于均线则看多（+1），否则中性（0）
        sign = np.sign(ma20 - close)  # 注意：close>ma20时，ma20-close为负，sign为-1，即看空
        result = pd.Series(np.where(cond, sign, 0), index=data.index)
        return result
