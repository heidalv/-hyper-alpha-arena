"""AI因子: 波动率稳定性得分 | 置信:65% | 衡量近期波动率的异常程度，通过短期波动率与长期波动率的比值以及波动率的变化率来识别市场状态不稳定的时期。比值偏离1或变化率过大时返回负值，表明regime uncertain。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Stability_Score(BaseFactor):
    """衡量近期波动率的异常程度，通过短期波动率与长期波动率的比值以及波动率的变化率来识别市场状态不稳定的时期。比值偏离1或变化率过大时返回负值，表明regime uncertain。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_stability",
            name="Volatility Stability Score",
            display_name="波动率稳定性得分",
            description="衡量近期波动率的异常程度，通过短期波动率与长期波动率的比值以及波动率的变化率来识别市场状态不稳定的时期。比值偏离1或变化率过大时返回负值，表明regime uncertain。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算对数收益率
        close = data['close']
        ret = np.log(close / close.shift(1))
        # 短期波动率：10期滚动标准差
        short_vol = ret.rolling(window=10).std()
        # 长期波动率：50期滚动标准差
        long_vol = ret.rolling(window=50).std()
        # 波动率变化率：近期波动率变化
        vol_change = short_vol.pct_change(periods=5)
        # 稳定得分：短期/长期比值接近1越好，且变化率绝对值小
        ratio = short_vol / long_vol
        # 使用指数函数映射到[-1,1]，偏离1越远越负
        score = 1 - np.abs(ratio - 1) * 2
        # 引入变化率惩罚，变化大则降低得分
        penalty = np.clip(np.abs(vol_change) * 10, 0, 1)
        result = score * (1 - penalty)
        result = result.fillna(0).clip(-1, 1)
        return result
