"""AI因子: 趋势强度风险指标 | 置信:60% | 基于ADX指标，当ADX低于25时认为市场处于无趋势震荡状态，容易导致多头止损，返回负值；ADX高于25时返回正值，值域通过tanh映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ADXRiskIndicator(BaseFactor):
    """基于ADX指标，当ADX低于25时认为市场处于无趋势震荡状态，容易导致多头止损，返回负值；ADX高于25时返回正值，值域通过tanh映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_adx_risk",
            name="ADX Risk Indicator",
            display_name="趋势强度风险指标",
            description="基于ADX指标，当ADX低于25时认为市场处于无趋势震荡状态，容易导致多头止损，返回负值；ADX高于25时返回正值，值域通过tanh映射到[-1,1]。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # 计算 TR
        tr = np.maximum(high - low, np.abs(high - close.shift()), np.abs(low - close.shift()))
        atr = tr.rolling(14).mean()
        # 计算方向移动指标
        up = high - high.shift()
        down = low.shift() - low
        plus_dm = np.where((up > down) & (up > 0), up, 0.0)
        minus_dm = np.where((down > up) & (down > 0), down, 0.0)
        plus_di = 100 * (plus_dm.rolling(14).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(14).mean() / atr)
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(14).mean()
        # 映射到[-1,1]，阈值25
        result = 2 * (adx / 50) - 1  # adx大致0-100，中心化
        result = np.clip(result, -1, 1)
        return pd.Series(result, index=data.index)
