"""AI因子: 市场状态未知风险 | 置信:65% | 综合价格偏离长期均线程度和近期波动率变化，当价格在均线附近震荡且波动率收缩时，市场处于模糊状态（regime unknown），此时做多风险高。返回负值表示高危险区域。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Regime_Unknown_Risk(BaseFactor):
    """综合价格偏离长期均线程度和近期波动率变化，当价格在均线附近震荡且波动率收缩时，市场处于模糊状态（regime unknown），此时做多风险高。返回负值表示高危险区域。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_risk_mode",
            name="Regime Unknown Risk",
            display_name="市场状态未知风险",
            description="综合价格偏离长期均线程度和近期波动率变化，当价格在均线附近震荡且波动率收缩时，市场处于模糊状态（regime unknown），此时做多风险高。返回负值表示高危险区域。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']

        # 长期均线（60日）
        ma_long = close.rolling(60).mean()

        # 价格偏离度（百分比）
        deviation = (close - ma_long) / ma_long * 100

        # 波动率收缩指标：当前ATR与过去60日ATR均值之比
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(14).mean()
        atr_60 = atr.rolling(60).mean()
        vol_shrink = atr / atr_60  # 小于1表示收缩

        # 偏离度绝对值小（-1%到1%之间）且波动率收缩 => 未知状态
        near_ma = (deviation.abs() < 1.0)
        shrink = (vol_shrink < 0.85)
        risk_zone = near_ma & shrink

        # 风险程度：结合偏离度符号和收缩程度，负值表示危险
        # 如果处于区间震荡且缩量，返回-1；否则根据偏离度轻微调节
        result = pd.Series(np.where(risk_zone, -1.0, 0.0), index=close.index)
        # 叠加成交量萎缩：连续缩量加重风险
        vol_ratio = volume / volume.rolling(20).mean()
        low_vol = (vol_ratio < 0.7).rolling(5).sum() >= 3
        result = np.where(low_vol & near_ma, -0.8, result)
        return pd.Series(result, index=close.index).fillna(0)
