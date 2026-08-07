"""AI因子: 低波震荡风险探测器 | 置信:65% | 识别低波动且无趋势的震荡市。在此类市场中趋势策略容易频繁止损或持仓超时亏损。因子值-1表示强震荡/低波环境（不宜做趋势交易），+1表示高波动趋势环境。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilitySqueezeChopDetector(BaseFactor):
    """识别低波动且无趋势的震荡市。在此类市场中趋势策略容易频繁止损或持仓超时亏损。因子值-1表示强震荡/低波环境（不宜做趋势交易），+1表示高波动趋势环境。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_chop",
            name="Volatility Squeeze & Chop Detector",
            display_name="低波震荡风险探测器",
            description="识别低波动且无趋势的震荡市。在此类市场中趋势策略容易频繁止损或持仓超时亏损。因子值-1表示强震荡/低波环境（不宜做趋势交易），+1表示高波动趋势环境。",
            category="volatility",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close'].astype(float)
        high = data['high'].astype(float)
        low = data['low'].astype(float)
        # 1. 波动率挤压：布林带宽度收缩
        boll_width = (close.rolling(20).mean() + 2*close.rolling(20).std() - (close.rolling(20).mean() - 2*close.rolling(20).std())) / close.rolling(20).mean()
        boll_rank = boll_width.rolling(100).rank(pct=True)
        # 2. 趋势方向明确度：ADX
        tr = np.maximum(high - low, np.abs(high - close.shift()), np.abs(low - close.shift()))
        atr = tr.rolling(14).mean()
        up_move = high - high.shift()
        down_move = low.shift() - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        plus_di = 100 * pd.Series(plus_dm, index=data.index).rolling(14).mean() / atr
        minus_di = 100 * pd.Series(minus_dm, index=data.index).rolling(14).mean() / atr
        dx = np.abs(plus_di - minus_di) / (plus_di + minus_di) * 100
        adx = dx.rolling(14).mean()
        adx_rank = adx.rolling(100).rank(pct=True)
        # 3. 区间震荡检测：近期高低点范围与波动比较
        highest = high.rolling(20).max()
        lowest = low.rolling(20).min()
        range_ratio = (close - lowest) / (highest - lowest + 1e-9)
        # 在区间中位附近无趋势为震荡
        chop_score = 1 - np.abs(range_ratio - 0.5) * 2  # 接近0.5得1
        # 综合：低波挤压+低ADX+区间中部 -> 震荡风险
        raw = chop_score * (1 - boll_rank) * (1 - adx_rank)
        # 映射到[-1,1]：震荡为-1，趋势为+1
        result = pd.Series(1 - 2*raw, index=data.index).clip(-1, 1).fillna(0)
        return result
