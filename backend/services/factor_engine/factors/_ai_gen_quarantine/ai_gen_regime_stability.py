"""AI因子: 市场状态稳定性因子 | 置信:65% | 根据短期与长期波动率的变化率，判断当前市场是否处于不稳定状态（regime=unknown）。历史上大部分亏损发生在regime=unknown时，因此该因子通过波动率结构变化发出风险警示。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeStabilityIndicator(BaseFactor):
    """根据短期与长期波动率的变化率，判断当前市场是否处于不稳定状态（regime=unknown）。历史上大部分亏损发生在regime=unknown时，因此该因子通过波动率结构变化发出风险警示。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_stability",
            name="Regime Stability Indicator",
            display_name="市场状态稳定性因子",
            description="根据短期与长期波动率的变化率，判断当前市场是否处于不稳定状态（regime=unknown）。历史上大部分亏损发生在regime=unknown时，因此该因子通过波动率结构变化发出风险警示。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        ret = data['close'].pct_change()
        # 短期波动率：5日标准差
        vol_short = ret.rolling(5).std()
        # 长期波动率：40日标准差
        vol_long = ret.rolling(40).std()
        vol_long = vol_long.replace(0, np.nan)
        # 波动率变化率：短期相对长期的偏离
        vol_ratio = vol_short / vol_long - 1
        # 波动率绝对水平：使用ATR归一化
        atr = (data['high'] - data['low']).rolling(14).mean()
        atr_ratio = atr / data['close']
        # 信号：波动率急剧变化且绝对水平高时为不稳定
        signal = -np.abs(vol_ratio) * atr_ratio * 100
        # 归一化到[-1,1]
        signal = signal.clip(-1, 0)  # 负值表示不稳定
        return signal.fillna(0)
