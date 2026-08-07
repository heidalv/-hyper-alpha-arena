"""AI因子: 趋势不确定性 | 置信:50% | 通过ADX与价格波动率之比衡量当前市场是否处于无趋势的regime=unknown状态。当ADX低于25且波动率（20日ATR/收盘价）高于历史中位数时，认为趋势不明，容易产生逆转陷阱，此时发出中性偏空信号（因亏损多为做空）。数值映射：ADX低+高波动时输出-0.5，否则0。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trenduncertainty(BaseFactor):
    """通过ADX与价格波动率之比衡量当前市场是否处于无趋势的regime=unknown状态。当ADX低于25且波动率（20日ATR/收盘价）高于历史中位数时，认为趋势不明，容易产生逆转陷阱，此时发出中性偏空信号（因亏损多为做空）。数值映射：ADX低+高波动时输出-0.5，否则0。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_uncertainty",
            name="TrendUncertainty",
            display_name="趋势不确定性",
            description="通过ADX与价格波动率之比衡量当前市场是否处于无趋势的regime=unknown状态。当ADX低于25且波动率（20日ATR/收盘价）高于历史中位数时，认为趋势不明，容易产生逆转陷阱，此时发出中性偏空信号（因亏损多为做空）。数值映射：ADX低+高波动时输出-0.5，否则0。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # ATR
        tr = np.maximum(data['high'] - data['low'],
                        np.abs(data['high'] - data['close'].shift(1)),
                        np.abs(data['low'] - data['close'].shift(1)))
        atr = tr.rolling(14).mean()
        volatility = atr / data['close']
        vol_median = volatility.rolling(50).median()
        # ADX
        high = data['high']
        low = data['low']
        close = data['close']
        plus_dm = np.where((high - high.shift(1)) > (low.shift(1) - low), np.maximum(high - high.shift(1), 0), 0)
        minus_dm = np.where((low.shift(1) - low) > (high - high.shift(1)), np.maximum(low.shift(1) - low, 0), 0)
        tr_series = pd.Series(tr, index=data.index)
        atr_14 = tr_series.rolling(14).mean()
        plus_di = 100 * pd.Series(plus_dm, index=data.index).rolling(14).mean() / atr_14
        minus_di = 100 * pd.Series(minus_dm, index=data.index).rolling(14).mean() / atr_14
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(14).mean()
        # 条件：低趋势且高波动
        condition = (adx < 25) & (volatility > vol_median)
        result = pd.Series(0.0, index=data.index)
        result[condition] = -0.5  # 趋势不明，倾向于空头陷阱
        return result
