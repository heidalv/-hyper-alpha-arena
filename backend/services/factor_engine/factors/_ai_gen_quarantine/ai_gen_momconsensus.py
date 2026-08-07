"""AI因子: 多周期动量一致性 | 置信:65% | 比较短期、中期和长期动量方向的一致性。若三者方向一致则市场状态明确，反之则混沌，属于‘未知’状态。计算三个不同周期（5,20,60）的价格变化率符号，取其一致性比例，映射到[-1,1]。负值表示不一致，应避免开仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Momentum_Consensus(BaseFactor):
    """比较短期、中期和长期动量方向的一致性。若三者方向一致则市场状态明确，反之则混沌，属于‘未知’状态。计算三个不同周期（5,20,60）的价格变化率符号，取其一致性比例，映射到[-1,1]。负值表示不一致，应避免开仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momconsensus",
            name="Momentum_Consensus",
            display_name="多周期动量一致性",
            description="比较短期、中期和长期动量方向的一致性。若三者方向一致则市场状态明确，反之则混沌，属于‘未知’状态。计算三个不同周期（5,20,60）的价格变化率符号，取其一致性比例，映射到[-1,1]。负值表示不一致，应避免开仓。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        # 计算不同周期回报
        ret5 = close.pct_change(5)
        ret20 = close.pct_change(20)
        ret60 = close.pct_change(60)
        # 符号
        sign5 = np.sign(ret5)
        sign20 = np.sign(ret20)
        sign60 = np.sign(ret60)
        # 一致性数量
        sum_sign = sign5 + sign20 + sign60
        # 如果三个同号，sum_sign绝对值为3；两个同号为1或-1；全不同为0或-0? 实际可能-1,1,3,-3
        # 用tanh映射：一致性越高越接近1，不一致接近-1
        # 归一化：除以3得[-1,1]区间
        raw = sum_sign / 3.0
        # 但sum_sign可能为-3,-1,1,3，除以3得-1,-0.33,0.33,1
        # 平滑处理：使用指数移动平均避免跳变
        result = raw.ewm(span=3, adjust=False).mean()
        return result
