"""AI因子: 突破信心因子 | 置信:50% | 基于布林带突破和成交量确认，判断当前价格是否处于有效突破状态。若价格在布林带内部且成交量萎缩，则市场处于震荡无序状态，输出负值。避免在无趋势时使用反转信号导致亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class BreakoutConfidence(BaseFactor):
    """基于布林带突破和成交量确认，判断当前价格是否处于有效突破状态。若价格在布林带内部且成交量萎缩，则市场处于震荡无序状态，输出负值。避免在无趋势时使用反转信号导致亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_breakout_strength",
            name="Breakout Confidence",
            display_name="突破信心因子",
            description="基于布林带突破和成交量确认，判断当前价格是否处于有效突破状态。若价格在布林带内部且成交量萎缩，则市场处于震荡无序状态，输出负值。避免在无趋势时使用反转信号导致亏损。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        period = 20
        # 布林带
        ma = close.rolling(window=period).mean()
        std = close.rolling(window=period).std()
        upper = ma + 2*std
        lower = ma - 2*std
        # 价格相对于布林带的位置
        band_width = upper - lower
        pos = (close - ma) / (band_width + 1e-10)  # -0.5到0.5之间为内部
        # 成交量变化
        vol_ma = volume.rolling(window=20).mean()
        vol_ratio = volume / (vol_ma + 1e-10)
        # 突破信号：价格在带内且成交量低 -> 无序 -> 负值
        inside = (np.abs(pos) < 0.45).astype(float)  # 接近中心
        low_vol = (vol_ratio < 0.8).astype(float)
        # 反向评分：无序时负，有序时正
        score = -0.8 * inside * low_vol + 0.2 * (1 - inside) * (vol_ratio > 1.2)
        score = np.clip(score, -1, 1)
        return pd.Series(score, index=data.index)
