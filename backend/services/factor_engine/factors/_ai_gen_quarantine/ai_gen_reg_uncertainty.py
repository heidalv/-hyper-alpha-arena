"""AI因子: 状态不确定性指标 | 置信:60% | 通过计算短期波动率与长期波动率的比值，以及多周期趋势方向的一致性，量化市场处于混沌状态的概率。当短期波动放大但长期趋势不明，或不同周期趋势方向冲突时，因子值接近1，表示高不确定性；反之接近-1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeUncertaintyIndicator(BaseFactor):
    """通过计算短期波动率与长期波动率的比值，以及多周期趋势方向的一致性，量化市场处于混沌状态的概率。当短期波动放大但长期趋势不明，或不同周期趋势方向冲突时，因子值接近1，表示高不确定性；反之接近-1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reg_uncertainty",
            name="Regime Uncertainty Indicator",
            display_name="状态不确定性指标",
            description="通过计算短期波动率与长期波动率的比值，以及多周期趋势方向的一致性，量化市场处于混沌状态的概率。当短期波动放大但长期趋势不明，或不同周期趋势方向冲突时，因子值接近1，表示高不确定性；反之接近-1。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 输入data: pd.DataFrame with columns: open, high, low, close, volume
        import pandas as pd
        import numpy as np
        # 计算短期波动率 (5日收盘价标准差)
        short_vol = data['close'].rolling(5).std()
        # 计算长期波动率 (20日)
        long_vol = data['close'].rolling(20).std()
        vol_ratio = short_vol / long_vol
        # 计算短期趋势 (5日EMA斜率)
        ema5 = data['close'].ewm(span=5).mean()
        slope5 = ema5.diff(1)
        # 计算长期趋势 (20日EMA斜率)
        ema20 = data['close'].ewm(span=20).mean()
        slope20 = ema20.diff(1)
        # 趋势一致性：如果短期和长期趋势方向相同则为1，不同为-1
        trend_consistency = np.sign(slope5) * np.sign(slope20)
        # 综合：vol_ratio高且trend_consistency低时不确定性高
        uncertainty = vol_ratio * trend_consistency * -1  # 反转符号使高不确定性为正
        # 标准化到[-1,1]，使用滚动Z-score
        roll_mean = uncertainty.rolling(50).mean()
        roll_std = uncertainty.rolling(50).std()
        z = (uncertainty - roll_mean) / (roll_std + 1e-10)
        result = np.clip(z, -3, 3) / 3.0
        return result.fillna(0)
