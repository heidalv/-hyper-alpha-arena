"""AI因子: 市场状态不确定性因子 | 置信:65% | 结合多时间尺度趋势斜率与波动率一致性，量化市场状态是否明确。使用短期（5日）和长期（20日）均线斜率以及ATR的变异性，当短期与长期趋势方向不一致或波动率剧烈变化时，判定为未知/高风险状态，输出负值；当趋势一致且波动率稳定时输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Regime_Uncertainty_Indicator(BaseFactor):
    """结合多时间尺度趋势斜率与波动率一致性，量化市场状态是否明确。使用短期（5日）和长期（20日）均线斜率以及ATR的变异性，当短期与长期趋势方向不一致或波动率剧烈变化时，判定为未知/高风险状态，输出负值；当趋势一致且波动率稳定时输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reg_uncert",
            name="Regime Uncertainty Indicator",
            display_name="市场状态不确定性因子",
            description="结合多时间尺度趋势斜率与波动率一致性，量化市场状态是否明确。使用短期（5日）和长期（20日）均线斜率以及ATR的变异性，当短期与长期趋势方向不一致或波动率剧烈变化时，判定为未知/高风险状态，输出负值；当趋势一致且波动率稳定时输出正值。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        # 趋势斜率
        short_slope = close.rolling(5).apply(lambda x: np.polyfit(range(len(x)), x, 1)[0], raw=True)
        long_slope = close.rolling(20).apply(lambda x: np.polyfit(range(len(x)), x, 1)[0], raw=True)
        # 归一化斜率
        short_slope_norm = short_slope / close.shift(1).fillna(close.mean())
        long_slope_norm = long_slope / close.shift(1).fillna(close.mean())
        # 方向一致性：符号相同程度
        sign_product = np.sign(short_slope_norm) * np.sign(long_slope_norm)
        # 波动率变异系数
        tr = np.maximum(data['high'] - data['low'], np.maximum(abs(data['high'] - data['close'].shift(1)), abs(data['low'] - data['close'].shift(1))))
        atr = tr.rolling(14).mean()
        atr_std = tr.rolling(14).std()
        vol_cv = atr_std / atr.replace(0, np.nan)
        # 综合得分
        score = sign_product * (1 - vol_cv.fillna(0.5))
        result = score.clip(-1, 1)
        # 平滑处理
        result = result.rolling(3).mean()
        result = result.fillna(0)
        return result
