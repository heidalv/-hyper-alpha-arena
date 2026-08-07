"""AI因子: 未知状态检测 | 置信:65% | 通过波动率、趋势强度和成交量变化识别市场是否处于不明确的‘未知’状态。当20日平均真实波幅百分比低于历史20%分位数且ADX<20时，判定为未知状态，因子值接近-1；否则接近+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Unknown_Regime_Detector(BaseFactor):
    """通过波动率、趋势强度和成交量变化识别市场是否处于不明确的‘未知’状态。当20日平均真实波幅百分比低于历史20%分位数且ADX<20时，判定为未知状态，因子值接近-1；否则接近+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unn_regime",
            name="Unknown Regime Detector",
            display_name="未知状态检测",
            description="通过波动率、趋势强度和成交量变化识别市场是否处于不明确的‘未知’状态。当20日平均真实波幅百分比低于历史20%分位数且ADX<20时，判定为未知状态，因子值接近-1；否则接近+1。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high, low, close, volume = data['high'], data['low'], data['close'], data['volume']
        # ATR
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(20).mean()
        atr_pct = atr / close * 100
        # ADX
        plus_dm = np.where((high - high.shift(1)) > (low.shift(1) - low), np.maximum(high - high.shift(1), 0), 0)
        minus_dm = np.where((low.shift(1) - low) > (high - high.shift(1)), np.maximum(low.shift(1) - low, 0), 0)
        tr14 = tr.rolling(14).sum()
        plus_di14 = 100 * (plus_dm.rolling(14).sum() / tr14.replace(0, np.nan))
        minus_di14 = 100 * (minus_dm.rolling(14).sum() / tr14.replace(0, np.nan))
        dx = 100 * np.abs(plus_di14 - minus_di14) / (plus_di14 + minus_di14).replace(0, np.nan)
        adx = dx.rolling(14).mean()
        # 分位数
        atr_percentile = atr_pct.rolling(252, min_periods=20).apply(lambda x: (x.iloc[-1] < np.percentile(x.dropna(), 20)) * 1.0, raw=False)
        # 信号
        unknown = (atr_percentile == 1) & (adx < 20)
        result = pd.Series(np.where(unknown, -1.0, 1.0), index=data.index)
        return result
