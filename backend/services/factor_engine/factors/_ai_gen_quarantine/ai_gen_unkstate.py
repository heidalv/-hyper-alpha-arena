"""AI因子: 未知市场状态因子 | 置信:65% | 通过ADX和波动率比率识别市场处于趋势或震荡状态。当ADX低于阈值且波动率较低时，市场状态不明，易导致趋势策略亏损。输出正值表示趋势强，负值表示震荡/未知。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class UnknownRegimeIndicator(BaseFactor):
    """通过ADX和波动率比率识别市场处于趋势或震荡状态。当ADX低于阈值且波动率较低时，市场状态不明，易导致趋势策略亏损。输出正值表示趋势强，负值表示震荡/未知。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unkstate",
            name="UnknownRegimeIndicator",
            display_name="未知市场状态因子",
            description="通过ADX和波动率比率识别市场处于趋势或震荡状态。当ADX低于阈值且波动率较低时，市场状态不明，易导致趋势策略亏损。输出正值表示趋势强，负值表示震荡/未知。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算ADX
        period = 14
        high = data['high']
        low = data['low']
        close = data['close']
        plus_dm = high.diff()
        minus_dm = low.diff()
        plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0)
        minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0)
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        plus_di = 100 * pd.Series(plus_dm).rolling(period).mean() / atr
        minus_di = 100 * pd.Series(minus_dm).rolling(period).mean() / atr
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        adx = dx.rolling(period).mean()
        # 波动率比率：当前ATR与近期平均ATR之比
        atr_ratio = atr / atr.rolling(period*2).mean()
        # 合成信号
        trend_strength = (adx - 25) / 50  # 以25为中性，范围约[-0.5,1.5]
        vol_factor = (atr_ratio - 1) * 2  # 波动率偏离
        combined = trend_strength * 0.7 + vol_factor * 0.3
        result = pd.Series(np.clip(combined, -1, 1), index=data.index)
        return result
