"""AI因子: 均线斜率比因子 | 置信:55% | 计算短期均线（5周期）斜率与长期均线（20周期）斜率的比值，并对结果进行tanh压缩到[-1,1]。当两者斜率接近0或比值接近1时表示趋势弱，输出负值；当两者同向且斜率明显时输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Masloperatio(BaseFactor):
    """计算短期均线（5周期）斜率与长期均线（20周期）斜率的比值，并对结果进行tanh压缩到[-1,1]。当两者斜率接近0或比值接近1时表示趋势弱，输出负值；当两者同向且斜率明显时输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ma_slope",
            name="MASlopeRatio",
            display_name="均线斜率比因子",
            description="计算短期均线（5周期）斜率与长期均线（20周期）斜率的比值，并对结果进行tanh压缩到[-1,1]。当两者斜率接近0或比值接近1时表示趋势弱，输出负值；当两者同向且斜率明显时输出正值。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        ma5 = close.rolling(window=5).mean()
        ma20 = close.rolling(window=20).mean()
        # 斜率：线性回归系数近似为差分
        slope5 = ma5.diff().rolling(window=5).mean()
        slope20 = ma20.diff().rolling(window=5).mean()
        # 防止除零
        eps = 1e-10
        ratio = np.abs(slope5) / (np.abs(slope20) + eps)
        # 当两者都接近0时，ratio可能不稳定，用乘积判断
        strength = np.sign(slope5 * slope20) * (ratio - 1)  # 同向为正，反向为负
        # 用tanh压缩到[-1,1]并乘以幅度
        result = np.tanh(strength * 10)
        return pd.Series(result, index=data.index).fillna(0)
