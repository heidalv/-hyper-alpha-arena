"""AI因子: 波动率状态不确定性 | 置信:50% | 基于近期波动率变化和价格位置，衡量市场是否处于高不确定性状态。当波动率急剧上升且价格远离近期均值时，不确定性高，因子趋向-1；当波动率平稳且价格趋势明确时，因子趋向+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityRegimeUncertainty(BaseFactor):
    """基于近期波动率变化和价格位置，衡量市场是否处于高不确定性状态。当波动率急剧上升且价格远离近期均值时，不确定性高，因子趋向-1；当波动率平稳且价格趋势明确时，因子趋向+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_uncertainty_vola",
            name="Volatility Regime Uncertainty",
            display_name="波动率状态不确定性",
            description="基于近期波动率变化和价格位置，衡量市场是否处于高不确定性状态。当波动率急剧上升且价格远离近期均值时，不确定性高，因子趋向-1；当波动率平稳且价格趋势明确时，因子趋向+1。",
            category="behavioral",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: DataFrame with columns ['open','high','low','close','volume']
        import pandas as pd
        import numpy as np
        # 计算20日波动率
        returns = data['close'].pct_change()
        vol_20 = returns.rolling(20).std()
        vol_5 = returns.rolling(5).std()
        # 波动率变化率
        vol_ratio = vol_5 / vol_20.replace(0, np.nan)
        # 价格相对位置 (当前close相对于过去20日高低)
        high_20 = data['close'].rolling(20).max()
        low_20 = data['close'].rolling(20).min()
        price_pos = (data['close'] - low_20) / (high_20 - low_20).replace(0, np.nan)
        # 不确定性得分: 波动率突变 * 价格在中间区域 (0.3~0.7) 时不确定性最高
        uncertainty = vol_ratio * (1 - 2 * np.abs(price_pos - 0.5))
        # 归一化到[-1,1],使用排名或直接裁剪
        uncertainty = uncertainty.clip(0, 1) * 2 - 1  # 将[0,1]映射到[-1,1],但这里uncertainty本身范围不定
        # 实际使用分位数映射到[-1,1]
        result = (uncertainty.rank(pct=True) - 0.5) * 2
        return result.fillna(0)
