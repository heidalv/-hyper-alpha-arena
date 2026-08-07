"""AI因子: 波动率调整趋势置信度 | 置信:65% | 计算近期价格变动与波动率的比值，识别趋势是否过度延伸或脆弱。在regime unknown环境下，过高或过低的趋势置信度往往预示反转风险。返回[-1,+1]，正值表示趋势稳健，负值表示趋势脆弱。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedTrendConfidence(BaseFactor):
    """计算近期价格变动与波动率的比值，识别趋势是否过度延伸或脆弱。在regime unknown环境下，过高或过低的趋势置信度往往预示反转风险。返回[-1,+1]，正值表示趋势稳健，负值表示趋势脆弱。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vatc",
            name="Volatility-Adjusted Trend Confidence",
            display_name="波动率调整趋势置信度",
            description="计算近期价格变动与波动率的比值，识别趋势是否过度延伸或脆弱。在regime unknown环境下，过高或过低的趋势置信度往往预示反转风险。返回[-1,+1]，正值表示趋势稳健，负值表示趋势脆弱。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算20周期ATR
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(20).mean().replace(0, np.nan)
        # 计算20周期收益率
        ret = close.pct_change(20)
        # 波动率调整趋势强度（收益率除以ATR相对价格的比例）
        rel_atr = atr / close
        tconf = ret / rel_atr
        # 使用10周期滚动z-score归一化到[-1,1]
        mean = tconf.rolling(20).mean()
        std = tconf.rolling(20).std().replace(0, np.nan)
        z = (tconf - mean) / std
        result = np.clip(z, -1, 1)
        return result
