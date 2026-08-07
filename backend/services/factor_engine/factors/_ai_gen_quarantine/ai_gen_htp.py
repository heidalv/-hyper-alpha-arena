"""AI因子: 持仓超时惩罚 | 置信:60% | 衡量价格在布林带内停留的时间比例，长时间窄幅震荡表示趋势不明确，因子接近0；价格突破带宽且持续运动则给予方向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Holdtimeoutpenalty(BaseFactor):
    """衡量价格在布林带内停留的时间比例，长时间窄幅震荡表示趋势不明确，因子接近0；价格突破带宽且持续运动则给予方向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_htp",
            name="HoldTimeoutPenalty",
            display_name="持仓超时惩罚",
            description="衡量价格在布林带内停留的时间比例，长时间窄幅震荡表示趋势不明确，因子接近0；价格突破带宽且持续运动则给予方向信号。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        period = 20
        close = data['close']
        # 布林带中轨（MA）和带宽
        ma = close.rolling(period).mean()
        std = close.rolling(period).std()
        upper = ma + 2 * std
        lower = ma - 2 * std
        # 计算价格在带内的比例（最近N根K线）
        inside = ((close >= lower) & (close <= upper)).astype(float)
        inside_ratio = inside.rolling(period).mean()  # 0~1
        # 方向信号：当前价格相对中轨位置
        position = (close - ma) / (std.replace(0, np.nan))  # 标准化
        # 当inside_ratio高（震荡）时，因子向0收缩；低时按位置方向
        # 使用权重：1 - inside_ratio 作为方向强度因子
        strength = (1 - inside_ratio) * np.tanh(position * 0.5)
        return strength.fillna(0).clip(-1,1)
