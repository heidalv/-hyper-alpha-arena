"""AI因子: 区间扩张衰竭 | 置信:65% | 检测价格波动区间扩张但成交量萎缩的衰竭信号，常见于假突破后回归，导致超时亏损。因子负值表示扩张不可持续，应回避方向性仓位。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RangeExpansionExhaustion(BaseFactor):
    """检测价格波动区间扩张但成交量萎缩的衰竭信号，常见于假突破后回归，导致超时亏损。因子负值表示扩张不可持续，应回避方向性仓位。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ree",
            name="Range Expansion Exhaustion",
            display_name="区间扩张衰竭",
            description="检测价格波动区间扩张但成交量萎缩的衰竭信号，常见于假突破后回归，导致超时亏损。因子负值表示扩张不可持续，应回避方向性仓位。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close'].astype(float)
        volume = data['volume'].astype(float)
        # 布林带宽度
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper = ma20 + 2 * std20
        lower = ma20 - 2 * std20
        bb_width = (upper - lower) / ma20
        # 宽度变化率
        width_change = bb_width / bb_width.shift(5) - 1
        # 成交量变化率
        vol_ma = volume.rolling(20).mean()
        vol_change = volume / vol_ma - 1
        # 衰竭信号：宽度扩张但量能未配合
        exhaustion = np.where(width_change > 0, -vol_change, vol_change)
        # 平滑并映射
        exhaustion_smooth = pd.Series(exhaustion, index=data.index).rolling(5).mean()
        result = np.tanh(exhaustion_smooth * 2)  # 放大信号
        return pd.Series(result, index=data.index).fillna(0)
