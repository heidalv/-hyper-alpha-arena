"""AI因子: 状态感知动量 | 置信:65% | 通过波动率和趋势强度划分市场状态（趋势/震荡），仅在趋势状态给出做多信号，震荡状态返回负值或零，避免在未知状态追高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Regime_Awareness_Momentum(BaseFactor):
    """通过波动率和趋势强度划分市场状态（趋势/震荡），仅在趋势状态给出做多信号，震荡状态返回负值或零，避免在未知状态追高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_state_aware",
            name="Regime Awareness Momentum",
            display_name="状态感知动量",
            description="通过波动率和趋势强度划分市场状态（趋势/震荡），仅在趋势状态给出做多信号，震荡状态返回负值或零，避免在未知状态追高。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: DataFrame with columns ['open','high','low','close','volume']
        close = data['close']
        # 计算EMA快慢线识别趋势
        ema_short = close.ewm(span=10, adjust=False).mean()
        ema_long = close.ewm(span=30, adjust=False).mean()
        # 趋势强度：快慢线差值归一化
        trend_diff = (ema_short - ema_long) / close
        # 波动率：20日ATR百分比
        high = data['high']
        low = data['low']
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift()), abs(low - close.shift())))
        atr = tr.rolling(14).mean()
        atr_pct = atr / close
        # 市场状态判断：趋势强度绝对值大且波动率适中 -> 趋势，否则震荡
        trend_strength = np.abs(trend_diff)
        # 使用百分位数动态阈值
        trend_thresh = trend_strength.rolling(60).quantile(0.7)
        vol_thresh = atr_pct.rolling(60).quantile(0.7)
        # 趋势状态：趋势强度大于阈值，且波动率小于阈值（不是极端波动）
        trend_regime = (trend_strength > trend_thresh) & (atr_pct < vol_thresh)
        # 在趋势状态下，信号为趋势方向（正负）；震荡状态下为0.2的均值回归信号（反向）
        direction = np.sign(trend_diff)
        # 均值回归信号：短期偏离均线的程度
        zscore = (close - ema_short) / close.std()
        mean_rev_signal = -np.clip(zscore, -2, 2) / 2  # 超买反转向下，超卖反转向上
        result = np.where(trend_regime, direction, 0.2 * mean_rev_signal)
        return pd.Series(result, index=data.index).fillna(0).clip(-1,1)
