"""AI因子: 量价背离指标 | 置信:50% | 检测价格动量与成交量变化的背离。当价格上涨但成交量萎缩，或价格下跌但成交量放大时，趋势不可靠，容易导致趋势跟踪策略亏损。因子负值表示量价背离风险高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeMomentumDivergence(BaseFactor):
    """检测价格动量与成交量变化的背离。当价格上涨但成交量萎缩，或价格下跌但成交量放大时，趋势不可靠，容易导致趋势跟踪策略亏损。因子负值表示量价背离风险高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vom",
            name="Volume-Momentum Divergence",
            display_name="量价背离指标",
            description="检测价格动量与成交量变化的背离。当价格上涨但成交量萎缩，或价格下跌但成交量放大时，趋势不可靠，容易导致趋势跟踪策略亏损。因子负值表示量价背离风险高。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        df = data.copy()
        close = df['close']
        volume = df['volume']
        # 计算价格变化率和成交量变化率
        price_ret = close.pct_change(periods=5)  # 5日收益率
        vol_ret = volume.pct_change(periods=5)
        # 标准化
        pr_norm = (price_ret - price_ret.rolling(50).mean()) / price_ret.rolling(50).std()
        vr_norm = (vol_ret - vol_ret.rolling(50).mean()) / vol_ret.rolling(50).std()
        # 背离得分: 如果两者同向则为正，反向则为负
        divergence = pr_norm * vr_norm
        # 取负号：负背离表示风险
        factor = -divergence
        # 将其映射到[-1,1]通过tanh
        factor = np.tanh(factor)
        return factor
