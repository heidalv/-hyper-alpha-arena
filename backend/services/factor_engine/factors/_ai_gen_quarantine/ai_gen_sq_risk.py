"""AI因子: 空头挤压风险因子 | 置信:60% | 衡量价格从近期低点快速反弹的强度，结合成交量放大，识别空头止损风险。计算价格在最近10周期内从最低点反弹的比例，乘以近3周期成交量变化率，经标准化后clip到[-1,1]。正值表示挤压风险高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortSqueezeRisk(BaseFactor):
    """衡量价格从近期低点快速反弹的强度，结合成交量放大，识别空头止损风险。计算价格在最近10周期内从最低点反弹的比例，乘以近3周期成交量变化率，经标准化后clip到[-1,1]。正值表示挤压风险高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sq_risk",
            name="Short_Squeeze_Risk",
            display_name="空头挤压风险因子",
            description="衡量价格从近期低点快速反弹的强度，结合成交量放大，识别空头止损风险。计算价格在最近10周期内从最低点反弹的比例，乘以近3周期成交量变化率，经标准化后clip到[-1,1]。正值表示挤压风险高。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        low = data['low']
        high = data['high']
        vol = data['volume']
        # 最近10天低点
        low_10 = low.rolling(10).min()
        high_10 = high.rolling(10).max()
        # 反弹比例：当前价格距离低点的位置占全距的比例
        range_ = high_10 - low_10
        rebound = (close - low_10) / (range_ + 1e-10)
        # 成交量变化：3日平均成交量比过去10日平均成交量
        vol_ma3 = vol.rolling(3).mean()
        vol_ma10 = vol.rolling(10).mean()
        vol_ratio = vol_ma3 / (vol_ma10 + 1e-10)
        # 综合：反弹比例乘以成交量放大程度
        raw = rebound * (vol_ratio - 1)
        # 标准化到[-1,1] 使用clip和tanh
        result = np.tanh(raw * 3)  # 缩放因子
        return result.fillna(0)
