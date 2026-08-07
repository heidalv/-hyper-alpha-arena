"""AI因子: 制度不稳定指数 | 置信:60% | 通过价格波动率与成交量波动率的比值变化，识别市场制度切换或未知状态，高值表示当前价格波动与成交量不匹配，易导致止损亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeInstabilityIndex(BaseFactor):
    """通过价格波动率与成交量波动率的比值变化，识别市场制度切换或未知状态，高值表示当前价格波动与成交量不匹配，易导致止损亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_instability",
            name="Regime Instability Index",
            display_name="制度不稳定指数",
            description="通过价格波动率与成交量波动率的比值变化，识别市场制度切换或未知状态，高值表示当前价格波动与成交量不匹配，易导致止损亏损。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: pd.DataFrame with columns ['open','high','low','close','volume']
        import numpy as np
        # 计算20日价格波动率（标准差）与20日成交量波动率
        price_vol = data['close'].pct_change().rolling(20).std()
        volume_vol = data['volume'].rolling(20).std()
        # 防止除以0
        ratio = price_vol / (volume_vol + 1e-10)
        # 标准化到[-1,1]：使用z-score然后clip
        z = (ratio - ratio.rolling(60).mean()) / (ratio.rolling(60).std() + 1e-10)
        result = np.clip(z, -3, 3) / 3.0
        return result.fillna(0.0)
