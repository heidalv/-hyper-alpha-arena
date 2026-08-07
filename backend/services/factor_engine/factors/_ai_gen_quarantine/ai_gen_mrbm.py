"""AI因子: 均值回归边界动量因子 | 置信:65% | 基于布林带位置和短期相对强弱RSI组合。当价格突破布林带上轨(>2倍标准差)且RSI>80时，做多风险极高（类似sl或master_running亏损场景），因子输出-1；当价格跌破下轨且RSI<20时，做多可能反弹但更符合做空场景，此处仅考虑做多，故输出-1警示。中等位置时根据短期动量方向输出±0.5。旨在规避未知状态下的极端做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MeanReversionBoundaryMomentum(BaseFactor):
    """基于布林带位置和短期相对强弱RSI组合。当价格突破布林带上轨(>2倍标准差)且RSI>80时，做多风险极高（类似sl或master_running亏损场景），因子输出-1；当价格跌破下轨且RSI<20时，做多可能反弹但更符合做空场景，此处仅考虑做多，故输出-1警示。中等位置时根据短期动量方向输出±0.5。旨在规避未知状态下的极端做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mrbm",
            name="MeanReversionBoundaryMomentum",
            display_name="均值回归边界动量因子",
            description="基于布林带位置和短期相对强弱RSI组合。当价格突破布林带上轨(>2倍标准差)且RSI>80时，做多风险极高（类似sl或master_running亏损场景），因子输出-1；当价格跌破下轨且RSI<20时，做多可能反弹但更符合做空场景，此处仅考虑做多，故输出-1警示。中等位置时根据短期动量方向输出±0.5。旨在规避未知状态下的极端做多。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        window = 20
        std_mult = 2
        mean = close.rolling(window).mean()
        std = close.rolling(window).std()
        upper = mean + std_mult * std
        lower = mean - std_mult * std
        # RSI
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        # 位置判定
        above_upper = close > upper
        below_lower = close < lower
        # 过度延伸做多风险极大 => -1
        extreme_long_risk = above_upper & (rsi > 80)
        extreme_short_risk = below_lower & (rsi < 20)  # 做多也可能被套
        # 中间区域：参考短期动量(3日)
        mom3 = close.pct_change(3)
        positive_mom = mom3 > 0.01
        negative_mom = mom3 < -0.01
        # 综合
        result = pd.Series(0.0, index=close.index)
        result[extreme_long_risk] = -1.0
        result[extreme_short_risk] = -1.0
        # 在非极端区域，根据短期动量给出温和信号
        cond_pos = (~extreme_long_risk) & (~extreme_short_risk) & positive_mom
        cond_neg = (~extreme_long_risk) & (~extreme_short_risk) & negative_mom
        result[cond_pos] = 0.5
        result[cond_neg] = -0.5
        return result
