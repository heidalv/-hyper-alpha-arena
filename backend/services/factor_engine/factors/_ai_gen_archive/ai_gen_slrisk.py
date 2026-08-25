"""AI因子: 止损聚集风险 | 置信:60% | 衡量价格接近近期关键支撑/阻力位的程度，结合ATR波动率，判断是否容易触发止损。当价格接近前高（阻力）且波动率缩小，空头止损风险高（建议避免做空）；接近前低（支撑）且波动率缩小，多头止损风险高（建议避免做多）。输出正值表示多头止损风险（应避免做多），负值表示空头止损风险（应避免做空）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class StopLossClusterRisk(BaseFactor):
    """衡量价格接近近期关键支撑/阻力位的程度，结合ATR波动率，判断是否容易触发止损。当价格接近前高（阻力）且波动率缩小，空头止损风险高（建议避免做空）；接近前低（支撑）且波动率缩小，多头止损风险高（建议避免做多）。输出正值表示多头止损风险（应避免做多），负值表示空头止损风险（应避免做空）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_slrisk",
            name="Stop-loss Cluster Risk",
            display_name="止损聚集风险",
            description="衡量价格接近近期关键支撑/阻力位的程度，结合ATR波动率，判断是否容易触发止损。当价格接近前高（阻力）且波动率缩小，空头止损风险高（建议避免做空）；接近前低（支撑）且波动率缩小，多头止损风险高（建议避免做多）。输出正值表示多头止损风险（应避免做多），负值表示空头止损风险（应避免做空）。",
            category="behavioral",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        lookback = 10
        # 前高和前低
        high = data['high']
        low = data['low']
        close = data['close']
        prev_high = high.rolling(lookback, min_periods=2).max().shift(1)
        prev_low = low.rolling(lookback, min_periods=2).min().shift(1)
        # ATR
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(14).mean()
        # 距离前高的比例 (距离从close到prev_high，越小越接近)
        dist_to_res = (prev_high - close) / (atr + 1e-10)  # 接近0即靠近阻力
        dist_to_sup = (close - prev_low) / (atr + 1e-10)  # 接近0即靠近支撑
        # 阈值：小于0.5个ATR视为接近
        long_stop_risk = np.where(dist_to_sup < 0.5, 1, 0)   # 靠近支撑，多头止损风险
        short_stop_risk = np.where(dist_to_res < 0.5, -1, 0) # 靠近阻力，空头止损风险
        # 结合波动率缩小：ATR近期下降
        atr_ratio = atr / atr.shift(5)  # 小于1表示波动率收窄
        risk = np.where((atr_ratio < 1) & (long_stop_risk == 1), 1, 
                        np.where((atr_ratio < 1) & (short_stop_risk == -1), -1, 0))
        result = pd.Series(risk, index=data.index).rolling(3, min_periods=1).mean()
        return result.clip(-1, 1)
