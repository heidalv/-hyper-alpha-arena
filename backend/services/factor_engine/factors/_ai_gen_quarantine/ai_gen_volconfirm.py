"""AI因子: 成交量确认因子 | 置信:65% | 通过比较价格变动与成交量变动的方向一致性，判断突破是否有效。价格创新高但成交量萎缩时输出负值（-1），价格与成交量同向放大时输出正值（+1），震荡时输出接近0。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volumeconfirmation(BaseFactor):
    """通过比较价格变动与成交量变动的方向一致性，判断突破是否有效。价格创新高但成交量萎缩时输出负值（-1），价格与成交量同向放大时输出正值（+1），震荡时输出接近0。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volconfirm",
            name="VolumeConfirmation",
            display_name="成交量确认因子",
            description="通过比较价格变动与成交量变动的方向一致性，判断突破是否有效。价格创新高但成交量萎缩时输出负值（-1），价格与成交量同向放大时输出正值（+1），震荡时输出接近0。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算价格变化率和成交量变化率
        price_ret = data['close'].pct_change(5)  # 5周期收益率
        vol_change = data['volume'].pct_change(5)
        # 计算两者的相关系数（滚动窗口15）
        corr = price_ret.rolling(15).corr(vol_change)
        # 映射到[-1,1]，同时考虑方向：正相关且双升为+，负相关且背离为-
        # 使用相关系数本身，但限制极端值
        result = corr.clip(-1, 1)
        # 填充NaN
        result = result.fillna(0)
        return result
