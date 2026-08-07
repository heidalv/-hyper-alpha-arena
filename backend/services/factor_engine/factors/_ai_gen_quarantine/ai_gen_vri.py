"""AI因子: 波动率状态指数 | 置信:60% | 计算近期20日滚动波动率与长期100日滚动波动率的比值，并对数化后映射到[-1,1]。比值接近1（正常波动）时因子接近1；比值显著偏离1（波动率异常高或低）时因子为负，代表市场处于不稳定或未知状态，类似亏损模式中的regime unknown。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityRegimeIndex(BaseFactor):
    """计算近期20日滚动波动率与长期100日滚动波动率的比值，并对数化后映射到[-1,1]。比值接近1（正常波动）时因子接近1；比值显著偏离1（波动率异常高或低）时因子为负，代表市场处于不稳定或未知状态，类似亏损模式中的regime unknown。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vri",
            name="Volatility Regime Index",
            display_name="波动率状态指数",
            description="计算近期20日滚动波动率与长期100日滚动波动率的比值，并对数化后映射到[-1,1]。比值接近1（正常波动）时因子接近1；比值显著偏离1（波动率异常高或低）时因子为负，代表市场处于不稳定或未知状态，类似亏损模式中的regime unknown。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        ret = data['close'].pct_change()
        vol_20 = ret.rolling(20).std()
        vol_100 = ret.rolling(100).std()
        # 避免除零
        ratio = vol_20 / vol_100.replace(0, np.nan)
        # 对数变换并映射到[-1,1]
        log_ratio = np.log(ratio.clip(0.01, 100))
        # 将log_ratio 范围约[-4.6,4.6]，除以4.6后clip
        result = -np.abs(log_ratio) / 4.6
        result = result.clip(-1, 1)
        return result
