"""AI因子: 趋势一致性指数 | 置信:60% | 通过比较短期（3日）和长期（10日）价格变化的符号一致性来衡量市场趋势的明确程度。当两者同向时，趋势清晰，因子为正；异向时，趋势模糊，因子为负。适用于识别regime unknown状态，负值时暗示应避免交易。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendConsistencyIndex(BaseFactor):
    """通过比较短期（3日）和长期（10日）价格变化的符号一致性来衡量市场趋势的明确程度。当两者同向时，趋势清晰，因子为正；异向时，趋势模糊，因子为负。适用于识别regime unknown状态，负值时暗示应避免交易。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tci",
            name="Trend Consistency Index",
            display_name="趋势一致性指数",
            description="通过比较短期（3日）和长期（10日）价格变化的符号一致性来衡量市场趋势的明确程度。当两者同向时，趋势清晰，因子为正；异向时，趋势模糊，因子为负。适用于识别regime unknown状态，负值时暗示应避免交易。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        short_ret = data['close'].pct_change(1)
        long_ret = data['close'].pct_change(10)
        # 符号相乘：同向为1，反向为-1，零处理为0
        sign_short = np.sign(short_ret).fillna(0)
        sign_long = np.sign(long_ret).fillna(0)
        result = sign_short * sign_long
        # 确保值域[-1,1]
        return result.clip(-1, 1)
