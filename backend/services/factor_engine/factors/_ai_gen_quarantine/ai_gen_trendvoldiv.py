"""AI因子: 趋势波动背离 | 置信:60% | 价格短期趋势方向与波动率变化的背离程度。计算短期均线斜率（例如EMA12 - EMA26）与波动率变化（当前波动率减去过去N期波动率均值）的乘积。当趋势向上（正斜率）而波动率下降（负变化）时，表明趋势可能衰竭（因子负值）；反之趋势向下而波动率上升时，可能恐慌加速（因子负值）。因子正值表示趋势与波动率同向，市场状态清晰。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendVolatilityDivergence(BaseFactor):
    """价格短期趋势方向与波动率变化的背离程度。计算短期均线斜率（例如EMA12 - EMA26）与波动率变化（当前波动率减去过去N期波动率均值）的乘积。当趋势向上（正斜率）而波动率下降（负变化）时，表明趋势可能衰竭（因子负值）；反之趋势向下而波动率上升时，可能恐慌加速（因子负值）。因子正值表示趋势与波动率同向，市场状态清晰。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trendvoldiv",
            name="Trend-Volatility Divergence",
            display_name="趋势波动背离",
            description="价格短期趋势方向与波动率变化的背离程度。计算短期均线斜率（例如EMA12 - EMA26）与波动率变化（当前波动率减去过去N期波动率均值）的乘积。当趋势向上（正斜率）而波动率下降（负变化）时，表明趋势可能衰竭（因子负值）；反之趋势向下而波动率上升时，可能恐慌加速（因子负值）。因子正值表示趋势与波动率同向，市场状态清晰。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        import numpy as np
        close = data['close']
        # 趋势斜率：短期均线减长期均线
        ema_short = close.ewm(span=12, adjust=False).mean()
        ema_long = close.ewm(span=26, adjust=False).mean()
        slope = ema_short - ema_long  # 正为上升趋势
        # 波动率变化
        ret = np.log(close / close.shift(1))
        vol = ret.rolling(20).std()
        vol_mean = vol.rolling(20).mean()
        vol_change = vol - vol_mean
        # 归一化斜率与波动率变化到[-1,1]
        slope_norm = np.clip(slope / (close * 0.01 + 1e-10), -1, 1)  # 价格1%变化量
        vol_change_norm = np.clip(vol_change / (vol_mean + 1e-10) * 10, -1, 1)
        # 乘积：同向为正，背离为负
        divergence = slope_norm * vol_change_norm
        return divergence
