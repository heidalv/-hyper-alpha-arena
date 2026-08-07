"""AI因子: 反转信号 | 置信:60% | 结合RSI超买和成交量萎缩判断短期反转风险，用于做多时规避高位回调，输出[-1,1]正值表示反转风险高"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Reversal_Signal(BaseFactor):
    """结合RSI超买和成交量萎缩判断短期反转风险，用于做多时规避高位回调，输出[-1,1]正值表示反转风险高"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reversalsignal",
            name="Reversal_Signal",
            display_name="反转信号",
            description="结合RSI超买和成交量萎缩判断短期反转风险，用于做多时规避高位回调，输出[-1,1]正值表示反转风险高",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        volume = data['volume']
        # RSI 14
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - 100 / (1 + rs)
        # 成交量萎缩：当前成交量相对过去20日均值
        vol_ma = volume.rolling(20).mean()
        vol_ratio = volume / (vol_ma + 1e-10)
        # 反转信号：RSI > 70 且 vol_ratio < 0.8
        signal = (rsi > 70).astype(float) * (vol_ratio < 0.8).astype(float)
        # 平滑并映射到[-1,1]
        result = signal.rolling(5).mean() * 2 - 1
        return result
