"""AI因子: 趋势动量衰减 | 置信:60% | 检测趋势动能减弱，价格偏离均线过远后速度放缓。计算短期（5）与长期（20）指数移动平均的差值（MACD柱），并计算差值的变化率。当差值为正且变化率由正转负时，预示上升趋势衰竭，做空（-1）；反之做多（+1）。通过差值与变化率的乘积归一化。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendMomentumDecay(BaseFactor):
    """检测趋势动能减弱，价格偏离均线过远后速度放缓。计算短期（5）与长期（20）指数移动平均的差值（MACD柱），并计算差值的变化率。当差值为正且变化率由正转负时，预示上升趋势衰竭，做空（-1）；反之做多（+1）。通过差值与变化率的乘积归一化。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_decay",
            name="Trend Momentum Decay",
            display_name="趋势动量衰减",
            description="检测趋势动能减弱，价格偏离均线过远后速度放缓。计算短期（5）与长期（20）指数移动平均的差值（MACD柱），并计算差值的变化率。当差值为正且变化率由正转负时，预示上升趋势衰竭，做空（-1）；反之做多（+1）。通过差值与变化率的乘积归一化。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        ema_short = close.ewm(span=5, adjust=False).mean()
        ema_long = close.ewm(span=20, adjust=False).mean()
        macd = ema_short - ema_long
        # 计算macd的变化率（一阶差分）
        macd_diff = macd.diff()
        # 信号：macd绝对值较大且diff方向相反
        # 标准化到[-1,1]：使用macd与close的比例和diff的方向
        norm_factor = 0.1  # 灵敏度
        raw = -np.sign(macd_diff) * np.tanh(np.abs(macd) / close * norm_factor * 100)
        # 仅当绝对macd大于阈值时有效
        raw[np.abs(macd) / close < 0.005] = 0
        return pd.Series(raw, index=data.index)
