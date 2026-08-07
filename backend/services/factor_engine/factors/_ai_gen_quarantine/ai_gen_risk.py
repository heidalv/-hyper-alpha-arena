"""AI因子: 均值回复风险 | 置信:60% | 判断价格偏离均线程度与成交量萎缩的组合。当价格远离20日均线且成交量低于近期均值时，持仓风险高，输出负值；反之输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MeanReversionRisk(BaseFactor):
    """判断价格偏离均线程度与成交量萎缩的组合。当价格远离20日均线且成交量低于近期均值时，持仓风险高，输出负值；反之输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_risk",
            name="Mean Reversion Risk",
            display_name="均值回复风险",
            description="判断价格偏离均线程度与成交量萎缩的组合。当价格远离20日均线且成交量低于近期均值时，持仓风险高，输出负值；反之输出正值。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        volume = data['volume']
        ma20 = close.rolling(20).mean()
        # 偏离度
        deviation = (close - ma20) / ma20
        # 成交量萎缩度：当前量/20日均量
        vol_ma = volume.rolling(20).mean()
        vol_ratio = volume / (vol_ma + 1e-9)
        # 综合风险信号：大偏离且低成交量 => 风险高
        risk = deviation * (1 - vol_ratio)
        # 用tanh压缩到[-1,1]，取负号使得高风险为负值
        result = -np.tanh(risk * 5)
        return result.fillna(0)
