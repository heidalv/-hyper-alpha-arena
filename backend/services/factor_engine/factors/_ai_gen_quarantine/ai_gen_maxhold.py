"""AI因子: 最大持仓超时风险因子 | 置信:53% | 基于持仓时间与价格动量衰减，当价格在长期区间内震荡且ATR下降时，超时持仓容易导致亏损。该因子计算价格在最近N周期内的趋势强度与持仓时间风险，信号为负时表示应减少持仓或平仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MaxHoldTimeoutRisk(BaseFactor):
    """基于持仓时间与价格动量衰减，当价格在长期区间内震荡且ATR下降时，超时持仓容易导致亏损。该因子计算价格在最近N周期内的趋势强度与持仓时间风险，信号为负时表示应减少持仓或平仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_maxhold",
            name="MaxHoldTimeoutRisk",
            display_name="最大持仓超时风险因子",
            description="基于持仓时间与价格动量衰减，当价格在长期区间内震荡且ATR下降时，超时持仓容易导致亏损。该因子计算价格在最近N周期内的趋势强度与持仓时间风险，信号为负时表示应减少持仓或平仓。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 参数
        n = 30
        hold_period = 10
        # 计算ATR和方向
        high = data['high']
        low = data['low']
        close = data['close']
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(n).mean()
        atr_slope = atr.diff(hold_period) / (atr + 1e-10)
        # 价格趋势强度：使用ADX简化版
        up_move = high - high.shift()
        down_move = low.shift() - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        tr_sum = tr.rolling(hold_period).sum()
        plus_di = pd.Series(plus_dm).rolling(hold_period).sum() / (tr_sum + 1e-10) * 100
        minus_di = pd.Series(minus_dm).rolling(hold_period).sum() / (tr_sum + 1e-10) * 100
        dx = (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10) * 100
        adx = dx.rolling(hold_period).mean()
        # 当ATR下降（atr_slope为负）且ADX走弱（小于20）时，表明市场震荡无趋势，容易超时亏损
        low_adx = (adx < 20).astype(int)
        atr_decline = (atr_slope < 0).astype(int)
        signal = -low_adx * atr_decline
        # 标准化
        signal = signal * 1.0
        signal = signal.fillna(0.0)
        return signal
