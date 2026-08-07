"""AI因子: 成交量背离因子 | 置信:60% | 检测价格与成交量的背离现象。当价格创出N周期新高但成交量未能同步创新高时，表明动能衰竭，市场可能转入regime=unknown。通过计算价格相对位置与成交量相对位置的差异，输出负值表示背离。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeDivergenceFactor(BaseFactor):
    """检测价格与成交量的背离现象。当价格创出N周期新高但成交量未能同步创新高时，表明动能衰竭，市场可能转入regime=unknown。通过计算价格相对位置与成交量相对位置的差异，输出负值表示背离。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_div",
            name="Volume Divergence Factor",
            display_name="成交量背离因子",
            description="检测价格与成交量的背离现象。当价格创出N周期新高但成交量未能同步创新高时，表明动能衰竭，市场可能转入regime=unknown。通过计算价格相对位置与成交量相对位置的差异，输出负值表示背离。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算价格在20周期内的百分位排名
        price_rank = data['close'].rolling(window=20).apply(lambda x: (x[-1] - x.min()) / (x.max() - x.min() + 1e-10), raw=True)
        # 计算成交量在20周期内的百分位排名
        vol_rank = data['volume'].rolling(window=20).apply(lambda x: (x[-1] - x.min()) / (x.max() - x.min() + 1e-10), raw=True)
        # 背离：价格高但成交量低（价格排名 > 成交量排名）
        div = price_rank - vol_rank
        # 映射到[-1,1]，背离越大负值越强
        result = -np.tanh(div * 5)
        return result
