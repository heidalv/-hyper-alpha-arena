"""AI因子: 市场未知状态风险评分 | 置信:70% | 基于ATR波动率与ADX趋势强度识别高风险未知市场状态。当波动率急剧上升且趋势强度弱（ADX<25）时，因子接近-1（做空/回避）；当波动率低且趋势强时接近+1（顺势）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Unknown_Regime_Risk_Score(BaseFactor):
    """基于ATR波动率与ADX趋势强度识别高风险未知市场状态。当波动率急剧上升且趋势强度弱（ADX<25）时，因子接近-1（做空/回避）；当波动率低且趋势强时接近+1（顺势）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_riskregime",
            name="Unknown Regime Risk Score",
            display_name="市场未知状态风险评分",
            description="基于ATR波动率与ADX趋势强度识别高风险未知市场状态。当波动率急剧上升且趋势强度弱（ADX<25）时，因子接近-1（做空/回避）；当波动率低且趋势强时接近+1（顺势）。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']

        # ATR
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        atr_change = atr.pct_change(10)

        # ADX
        plus_dm = high.diff()
        minus_dm = low.diff().abs()
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0
        tr_smooth = tr.rolling(14).mean()
        plus_di = 100 * (plus_dm.rolling(14).mean() / tr_smooth)
        minus_di = 100 * (minus_dm.rolling(14).mean() / tr_smooth)
        dx = (plus_di - minus_di).abs() / (plus_di + minus_di) * 100
        adx = dx.rolling(14).mean()

        # 波动率冲击 -> 未知状态
        vol_shock = (atr_change > atr_change.rolling(20).std() * 2).astype(float)
        weak_trend = (adx < 25).astype(float)
        unknown = vol_shock * weak_trend  # 0或1

        # 合成因子：高未知风险->负值，低未知风险->正值（基于趋势方向）
        trend_dir = np.sign(close.rolling(20).mean() - close.rolling(50).mean())
        base = trend_dir * (1 - unknown)
        result = base.fillna(0).clip(-1, 1)
        return result
