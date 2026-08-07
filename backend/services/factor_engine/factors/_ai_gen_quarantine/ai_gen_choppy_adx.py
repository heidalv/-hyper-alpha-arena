"""AI因子: 低ADX震荡市过滤 | 置信:70% | ADX低于阈值（如20）表明市场无明显趋势，处于震荡状态。亏损模式中大量timeout和master_running亏损发生在regime=unknown，很可能对应低趋势环境，容易造成持仓时间过长后回落或止损。因子输出负值以抑制做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ADXTrendlessChoppinessFilter(BaseFactor):
    """ADX低于阈值（如20）表明市场无明显趋势，处于震荡状态。亏损模式中大量timeout和master_running亏损发生在regime=unknown，很可能对应低趋势环境，容易造成持仓时间过长后回落或止损。因子输出负值以抑制做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_choppy_adx",
            name="ADX Trendless Choppiness Filter",
            display_name="低ADX震荡市过滤",
            description="ADX低于阈值（如20）表明市场无明显趋势，处于震荡状态。亏损模式中大量timeout和master_running亏损发生在regime=unknown，很可能对应低趋势环境，容易造成持仓时间过长后回落或止损。因子输出负值以抑制做多。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        high = data['high']
        low = data['low']
        close = data['close']
        # 计算ADX
        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        plus_di = 100.0 * (plus_dm.rolling(14).mean() / atr)
        minus_di = 100.0 * (minus_dm.rolling(14).mean() / atr)
        dx = (plus_di - minus_di).abs() / (plus_di + minus_di) * 100.0
        adx = dx.rolling(14).mean()
        # 震荡市：ADX低于20，映射为-1；高于40为+1；中间线性
        result = -1.0 + 2.0 * (adx.clip(20, 40) - 20) / 20.0
        result = result.fillna(-1).clip(-1, 1)
        return result
