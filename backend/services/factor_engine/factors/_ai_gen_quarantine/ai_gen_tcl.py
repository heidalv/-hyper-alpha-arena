"""AI因子: 趋势清晰度 | 置信:65% | 使用ADX衡量趋势强度，当ADX低于20时视为无趋势状态，此时做多风险高，输出负值；趋势清晰时输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class trend_clarity(BaseFactor):
    """使用ADX衡量趋势强度，当ADX低于20时视为无趋势状态，此时做多风险高，输出负值；趋势清晰时输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tcl",
            name="trend_clarity",
            display_name="趋势清晰度",
            description="使用ADX衡量趋势强度，当ADX低于20时视为无趋势状态，此时做多风险高，输出负值；趋势清晰时输出正值。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # 计算TR
        tr = pd.concat([high - low, abs(high - close.shift(1)), abs(low - close.shift(1))], axis=1).max(axis=1)
        # 计算+DM, -DM
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        # 平滑14周期
        period = 14
        tr_smooth = tr.rolling(period).mean()
        plus_di = 100 * pd.Series(plus_dm).rolling(period).mean() / tr_smooth
        minus_di = 100 * pd.Series(minus_dm).rolling(period).mean() / tr_smooth
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(period).mean()
        # 归一化到[-1,1]，阈值20
        raw = (adx - 20) / 40  # 0~40映射到-0.5~0.5，再clip
        result = raw.clip(-1, 1)
        return result.fillna(0)
