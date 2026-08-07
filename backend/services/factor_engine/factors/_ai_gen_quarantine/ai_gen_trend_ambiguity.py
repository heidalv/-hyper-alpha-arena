"""AI因子: 趋势模糊指数 | 置信:70% | 基于ADX指标改进，衡量趋势清晰度。当ADX低于阈值时，市场处于无趋势或震荡状态，容易触发未知模式亏损。因子值正比于趋势清晰度（ADX），负值表示模糊。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Ambiguity_Index(BaseFactor):
    """基于ADX指标改进，衡量趋势清晰度。当ADX低于阈值时，市场处于无趋势或震荡状态，容易触发未知模式亏损。因子值正比于趋势清晰度（ADX），负值表示模糊。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_ambiguity",
            name="Trend Ambiguity Index",
            display_name="趋势模糊指数",
            description="基于ADX指标改进，衡量趋势清晰度。当ADX低于阈值时，市场处于无趋势或震荡状态，容易触发未知模式亏损。因子值正比于趋势清晰度（ADX），负值表示模糊。",
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
        # 计算+DM和-DM
        up = high.diff()
        down = -low.diff()
        plus_dm = np.where((up > down) & (up > 0), up, 0)
        minus_dm = np.where((down > up) & (down > 0), down, 0)
        # 平滑（14周期）
        period = 14
        tr = np.maximum(high - low, np.abs(high - close.shift()), np.abs(low - close.shift()))
        atr = tr.rolling(period).mean()
        plus_di = 100 * pd.Series(plus_dm).rolling(period).mean() / (atr + 1e-10)
        minus_di = 100 * pd.Series(minus_dm).rolling(period).mean() / (atr + 1e-10)
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(period).mean()
        # 映射：ADX<20为模糊（负值），ADX>40为清晰（正值）
        result = (adx - 30) / 20.0  # 大致范围[-1,1]
        result = result.clip(-1, 1)
        return result
