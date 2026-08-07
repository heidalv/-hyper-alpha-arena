"""AI因子: 未知状态均值回归因子 | 置信:60% | 基于历史亏损多发生在unknown regime，推测此时市场呈震荡特征。该因子计算短期（3日）收益率，并结合成交量萎缩（低于20日均量）作为反转信号。当短期涨幅过大且量能不足时给出空头信号(-1)，跌幅过大时给出多头信号(+1)，否则为0。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Unknown_Regime_Mean_Reversion_Factor(BaseFactor):
    """基于历史亏损多发生在unknown regime，推测此时市场呈震荡特征。该因子计算短期（3日）收益率，并结合成交量萎缩（低于20日均量）作为反转信号。当短期涨幅过大且量能不足时给出空头信号(-1)，跌幅过大时给出多头信号(+1)，否则为0。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unknown_reversal",
            name="Unknown Regime Mean Reversion Factor",
            display_name="未知状态均值回归因子",
            description="基于历史亏损多发生在unknown regime，推测此时市场呈震荡特征。该因子计算短期（3日）收益率，并结合成交量萎缩（低于20日均量）作为反转信号。当短期涨幅过大且量能不足时给出空头信号(-1)，跌幅过大时给出多头信号(+1)，否则为0。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret3 = data['close'].pct_change(3)
        vol_ma = data['volume'].rolling(20).mean()
        vol_ratio = data['volume'] / (vol_ma + 1e-10)
        # 量能萎缩条件：成交量低于均量0.8倍
        low_vol = vol_ratio < 0.8
        # 反转信号：短期超涨/超跌且缩量
        overbought = (ret3 > 0.03) & low_vol
        oversold = (ret3 < -0.03) & low_vol
        factor = pd.Series(0, index=data.index)
        factor[oversold] = 1.0
        factor[overbought] = -1.0
        return factor
