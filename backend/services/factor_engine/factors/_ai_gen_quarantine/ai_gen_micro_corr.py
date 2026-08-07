"""AI因子: 微观结构相关性 | 置信:55% | 计算短周期内价格变动与成交量的相关性，识别假突破或流动性陷阱。当价格大幅变动但成交量未有效放大时（相关性接近0或负），信号为负（-1）；当价量齐升或齐跌时，信号为正（+1）。该因子对未知状态下的异常跳动敏感。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Microstructure_Correlation(BaseFactor):
    """计算短周期内价格变动与成交量的相关性，识别假突破或流动性陷阱。当价格大幅变动但成交量未有效放大时（相关性接近0或负），信号为负（-1）；当价量齐升或齐跌时，信号为正（+1）。该因子对未知状态下的异常跳动敏感。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_micro_corr",
            name="Microstructure Correlation",
            display_name="微观结构相关性",
            description="计算短周期内价格变动与成交量的相关性，识别假突破或流动性陷阱。当价格大幅变动但成交量未有效放大时（相关性接近0或负），信号为负（-1）；当价量齐升或齐跌时，信号为正（+1）。该因子对未知状态下的异常跳动敏感。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算价格变化和成交量变化（百分比）
        price_chg = data['close'].pct_change()
        vol_chg = data['volume'].pct_change()
        # 滚动窗口相关系数（3个周期）
        corr = price_chg.rolling(3).corr(vol_chg)
        # 同时考虑价格变动的绝对值是否显著（>1%）
        abs_price_chg = price_chg.abs()
        significant = abs_price_chg > 0.01
        # 信号：显著价格变动但相关性低（<0.3或NaN）则负，否则正
        signal = pd.Series(0.5, index=data.index)  # 默认中性偏正
        neg_cond = significant & ((corr < 0.3) | corr.isna())
        pos_cond = significant & (corr >= 0.3)
        signal[neg_cond] = -1.0
        signal[pos_cond] = 1.0
        return signal.fillna(0.0)
