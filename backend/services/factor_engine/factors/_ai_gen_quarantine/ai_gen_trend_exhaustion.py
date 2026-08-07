"""AI因子: 趋势衰竭指标 | 置信:60% | 通过计算价格在近期高低点范围内的相对位置与成交量分布，判断趋势是否接近衰竭。当价格创新高/新低但成交量递减，且RSI进入超买/超卖区时，趋势可能反转。因子结合价格位置（距N日高点的比例）、成交量的变化率以及RSI，输出[-1,1]信号，正值表示看涨衰竭（可能下跌），负值表示看跌衰竭（可能上涨）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendExhaustionIndicator(BaseFactor):
    """通过计算价格在近期高低点范围内的相对位置与成交量分布，判断趋势是否接近衰竭。当价格创新高/新低但成交量递减，且RSI进入超买/超卖区时，趋势可能反转。因子结合价格位置（距N日高点的比例）、成交量的变化率以及RSI，输出[-1,1]信号，正值表示看涨衰竭（可能下跌），负值表示看跌衰竭（可能上涨）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_exhaustion",
            name="Trend Exhaustion Indicator",
            display_name="趋势衰竭指标",
            description="通过计算价格在近期高低点范围内的相对位置与成交量分布，判断趋势是否接近衰竭。当价格创新高/新低但成交量递减，且RSI进入超买/超卖区时，趋势可能反转。因子结合价格位置（距N日高点的比例）、成交量的变化率以及RSI，输出[-1,1]信号，正值表示看涨衰竭（可能下跌），负值表示看跌衰竭（可能上涨）。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
    
        n = 14
        # 价格相对位置：(close - low_roll) / (high_roll - low_roll)
        high_roll = high.rolling(n).max()
        low_roll = low.rolling(n).min()
        range_ = high_roll - low_roll
        range_ = range_.replace(0, np.nan)
        position = (close - low_roll) / range_  # [0,1]
    
        # 成交量变化：当前成交量与过去n日均成交量的比率
        vol_ma = volume.rolling(n).mean()
        vol_ratio = volume / vol_ma
        # 当vol_ratio<1表示缩量
    
        # RSI
        diff = close.diff()
        gain = diff.clip(lower=0).rolling(n).mean()
        loss = (-diff.clip(upper=0)).rolling(n).mean()
        rsi = 100 - 100 / (1 + gain / loss)
        rsi = rsi / 100.0  # [0,1]
    
        # 结合：位置极端（接近1或0）且缩量，且RSI极端
        # 看涨衰竭：position近1且rsi>0.7且vol_ratio<1 => 卖出信号（负值）
        # 看跌衰竭：position近0且rsi<0.3且vol_ratio<1 => 买入信号（正值）
        bull_exhaust = ((position > 0.8).astype(float) * (rsi > 0.7).astype(float) * (1 - vol_ratio.clip(0,1)))
        bear_exhaust = ((position < 0.2).astype(float) * (rsi < 0.3).astype(float) * (1 - vol_ratio.clip(0,1)))
        signal = -bull_exhaust + bear_exhaust
        # 平滑并归一化
        signal = signal.rolling(2).mean()
        return signal.fillna(0)
