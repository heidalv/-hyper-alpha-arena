"""AI因子: 日内反转指示器 | 置信:62% | 检测短期价格反转概率，基于上下影线比例和成交量异常。当出现长上影线或低成交量上涨时，容易引发止损或反向下跌。负值表示反转风险高，不适合做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Intraday_Reversal_Indicator(BaseFactor):
    """检测短期价格反转概率，基于上下影线比例和成交量异常。当出现长上影线或低成交量上涨时，容易引发止损或反向下跌。负值表示反转风险高，不适合做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_intra_reversal",
            name="Intraday Reversal Indicator",
            display_name="日内反转指示器",
            description="检测短期价格反转概率，基于上下影线比例和成交量异常。当出现长上影线或低成交量上涨时，容易引发止损或反向下跌。负值表示反转风险高，不适合做多。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        open_ = data['open']
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        # 上影线比例
        upper_shadow = (high - np.maximum(open_, close)) / (high - low + 1e-10)
        # 下影线比例
        lower_shadow = (np.minimum(open_, close) - low) / (high - low + 1e-10)
        # 日内强度: 收盘在区间位置
        pos_ratio = (close - low) / (high - low + 1e-10)
        # 成交量异常: 当前成交量相对于过去20日均值的比率
        vol_ma = volume.rolling(20).mean()
        vol_ratio = volume / (vol_ma + 1e-10)
        # 反转信号: 上影线长且成交量异常高且收盘弱 → 看跌反转
        # 定义组合: long方向风险高时，因子为负
        reversal_score = (upper_shadow - lower_shadow) * (pos_ratio - 0.5) * vol_ratio
        # 标准化到[-1,1]
        result = np.clip(reversal_score * 2, -1.0, 1.0)
        return result
