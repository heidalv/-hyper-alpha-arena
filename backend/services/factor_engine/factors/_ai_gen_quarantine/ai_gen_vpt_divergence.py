"""AI因子: 量价趋势背离 | 置信:50% | 基于Volume Price Trend (VPT)指标与价格线性回归斜率的背离程度。当价格上涨但VPT下降，或价格下跌但VPT上升，视为量价背离，预示反转或弱势。标准化到[-1,1]，负值表示看跌背离，正值表示看涨背离，0附近表示一致。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceTrendDivergence(BaseFactor):
    """基于Volume Price Trend (VPT)指标与价格线性回归斜率的背离程度。当价格上涨但VPT下降，或价格下跌但VPT上升，视为量价背离，预示反转或弱势。标准化到[-1,1]，负值表示看跌背离，正值表示看涨背离，0附近表示一致。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vpt_divergence",
            name="Volume Price Trend Divergence",
            display_name="量价趋势背离",
            description="基于Volume Price Trend (VPT)指标与价格线性回归斜率的背离程度。当价格上涨但VPT下降，或价格下跌但VPT上升，视为量价背离，预示反转或弱势。标准化到[-1,1]，负值表示看跌背离，正值表示看涨背离，0附近表示一致。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        volume = data['volume']
        # 计算VPT
        vpt = (close.diff() / close.shift(1)) * volume
        vpt_cum = vpt.cumsum()
        # 计算过去20个周期的线性回归斜率
        from scipy import stats
        def slope(series):
            if len(series) < 20:
                return np.nan
            x = np.arange(len(series))
            slope, _, _, _, _ = stats.linregress(x, series.values)
            return slope
        # 价格斜率
        price_slope = close.rolling(window=20).apply(lambda x: slope(x), raw=False)
        # VPT斜率
        vpt_slope = vpt_cum.rolling(window=20).apply(lambda x: slope(x), raw=False)
        # 计算背离：价格斜率与VPT斜率差标准化
        diff = price_slope - vpt_slope
        # 使用tanh标准化
        norm = np.tanh(diff * 0.1)  # 调节系数
        return norm.clip(-1, 1)
