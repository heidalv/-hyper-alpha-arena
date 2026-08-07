"""AI因子: 趋势强度因子 | 置信:60% | 使用ADX指标衡量市场趋势强度。ADX低于20表示无趋势（可能为unknown regime），因子为负；ADX高于40表示强趋势，因子为正。线性映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ADX_Trend_Strength(BaseFactor):
    """使用ADX指标衡量市场趋势强度。ADX低于20表示无趋势（可能为unknown regime），因子为负；ADX高于40表示强趋势，因子为正。线性映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_adx_regime",
            name="ADX_Trend_Strength",
            display_name="趋势强度因子",
            description="使用ADX指标衡量市场趋势强度。ADX低于20表示无趋势（可能为unknown regime），因子为负；ADX高于40表示强趋势，因子为正。线性映射到[-1,1]。",
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
        h = high.diff()
        l = -low.diff()
        plus_dm = np.where((h > l) & (h > 0), h, 0)
        minus_dm = np.where((l > h) & (l > 0), l, 0)
        # 平滑（14周期）
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr = tr.rolling(window=14).mean()
        plus_di = 100 * pd.Series(plus_dm).rolling(window=14).mean() / atr
        minus_di = 100 * pd.Series(minus_dm).rolling(window=14).mean() / atr
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(window=14).mean()
        # 映射：ADX 20->-1, 30->0, 40->1
        result = np.clip((adx - 30) / 10, -1, 1)
        return result.fillna(0)
