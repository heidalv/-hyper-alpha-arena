"""AI因子: 动量波动率背离 | 置信:70% | 检测价格动量与波动率的背离现象。当价格创新高但波动率下降（或创新低但波动率上升）时，预示趋势衰竭。计算动量方向与波动率趋势的相关性，负值表示背离风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Momentum_Volatility_Divergence(BaseFactor):
    """检测价格动量与波动率的背离现象。当价格创新高但波动率下降（或创新低但波动率上升）时，预示趋势衰竭。计算动量方向与波动率趋势的相关性，负值表示背离风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_vol_div",
            name="Momentum-Volatility Divergence",
            display_name="动量波动率背离",
            description="检测价格动量与波动率的背离现象。当价格创新高但波动率下降（或创新低但波动率上升）时，预示趋势衰竭。计算动量方向与波动率趋势的相关性，负值表示背离风险。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        high = data['high']
        low = data['low']
        # 动量：10周期价格变化率
        momentum = close.pct_change(10) * 100
        # 波动率：20周期ATR
        tr = pd.concat([high - low, abs(high - close.shift(1)), abs(low - close.shift(1))], axis=1).max(axis=1)
        atr = tr.rolling(20).mean()
        atr_change = atr.pct_change(10) * 100
        # 计算最近10周期内动量与波动率变化的相关性
        corr = momentum.rolling(10).corr(atr_change)
        # 背离：当动量向上但波动率向下（corr<0）或动量向下但波动率向上（corr<0）
        # 用负相关程度作为信号
        divergence = -corr
        # 加入动量强弱惩罚
        abs_mom = momentum.abs()
        score = divergence * np.sign(momentum) * (abs_mom / 10).clip(0, 1)
        result = np.tanh(score * 2)
        return pd.Series(result, index=data.index).fillna(0)
