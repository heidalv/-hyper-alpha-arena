"""AI因子: 趋势可信度 | 置信:65% | 通过计算短期趋势强度与近期波动率的比值，判断当前趋势是否可靠。当趋势强且波动率低时，趋势延续概率高；反之，趋势弱或波动率高时，可能处于不稳定状态，易触发亏损模式。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendReliability(BaseFactor):
    """通过计算短期趋势强度与近期波动率的比值，判断当前趋势是否可靠。当趋势强且波动率低时，趋势延续概率高；反之，趋势弱或波动率高时，可能处于不稳定状态，易触发亏损模式。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trdvol",
            name="TrendReliability",
            display_name="趋势可信度",
            description="通过计算短期趋势强度与近期波动率的比值，判断当前趋势是否可靠。当趋势强且波动率低时，趋势延续概率高；反之，趋势弱或波动率高时，可能处于不稳定状态，易触发亏损模式。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        # 短期趋势强度：过去10期收盘价变化率的绝对值
        ret10 = close.pct_change(10).abs()
        # 波动率：过去20期ATR的相对值
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr20 = tr.rolling(20).mean()
        atr_ratio = atr20 / close
        # 趋势可信度：趋势强度 / 波动率，然后标准化到[-1,1]
        reliability = ret10 / (atr_ratio + 1e-10)
        # 使用滚动标准化避免极端值
        rolling_mean = reliability.rolling(50).mean()
        rolling_std = reliability.rolling(50).std()
        result = (reliability - rolling_mean) / (rolling_std + 1e-10)
        return result.clip(-1, 1)
