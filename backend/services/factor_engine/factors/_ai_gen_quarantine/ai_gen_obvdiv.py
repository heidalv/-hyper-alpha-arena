"""AI因子: OBV背离 | 置信:60% | 检测价格与OBV（能量潮）的背离。当价格创出新高而OBV未能同步新高时，暗示买盘衰竭，做多风险高（如master_running亏损）。使用过去N根K线的价格趋势和OBV趋势的对比，输出背离强度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class OBVDivergence(BaseFactor):
    """检测价格与OBV（能量潮）的背离。当价格创出新高而OBV未能同步新高时，暗示买盘衰竭，做多风险高（如master_running亏损）。使用过去N根K线的价格趋势和OBV趋势的对比，输出背离强度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_obvdiv",
            name="OBV Divergence",
            display_name="OBV背离",
            description="检测价格与OBV（能量潮）的背离。当价格创出新高而OBV未能同步新高时，暗示买盘衰竭，做多风险高（如master_running亏损）。使用过去N根K线的价格趋势和OBV趋势的对比，输出背离强度。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 计算OBV
        obv = (volume * (close.diff() > 0).astype(int) - volume * (close.diff() < 0).astype(int)).cumsum()
        # 计算过去20周期的价格和OBV线性回归斜率
        window = 20
        # 标准化价格和OBV
        price_z = (close - close.rolling(window).mean()) / close.rolling(window).std().replace(0, np.nan)
        obv_z = (obv - obv.rolling(window).mean()) / obv.rolling(window).std().replace(0, np.nan)
        # 计算两者的差值：价格标准化值减去OBV标准化值，正值表示价格相对OBV超买
        diff = price_z - obv_z
        # 使用滚动标准化并clip
        result = diff.rolling(window).mean() / diff.rolling(window).std().replace(0, np.nan)
        result = result.clip(-3, 3) / 3.0
        # 当diff为正且大时，价格相对OBV偏高，做空信号（负因子）
        return -result.fillna(0)
