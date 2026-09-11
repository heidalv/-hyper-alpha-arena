"""AI因子: 未知状态波动率自适应反向 | 置信:55% | 亏损集中在regime=unknown，暗示市场状态识别失效。利用短期波动率变化率与价格动量背离：当波动率快速上升但价格未同步创新高时，做空易超时亏损；因子在波动率上升且价格走弱时为正，反之做空风险高为负。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class UnknownRegimeVolatilityContrarian(BaseFactor):
    """亏损集中在regime=unknown，暗示市场状态识别失效。利用短期波动率变化率与价格动量背离：当波动率快速上升但价格未同步创新高时，做空易超时亏损；因子在波动率上升且价格走弱时为正，反之做空风险高为负。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unknown_regime_vol_adapt",
            name="Unknown_Regime_Volatility_Contrarian",
            display_name="未知状态波动率自适应反向",
            description="亏损集中在regime=unknown，暗示市场状态识别失效。利用短期波动率变化率与价格动量背离：当波动率快速上升但价格未同步创新高时，做空易超时亏损；因子在波动率上升且价格走弱时为正，反之做空风险高为负。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        vol_short = data['close'].pct_change().rolling(5).std()
        vol_long = data['close'].pct_change().rolling(20).std()
        vol_ratio = vol_short / (vol_long + 1e-9)
        price_change = data['close'].pct_change(5)
        result = (vol_ratio - price_change).clip(-1, 1)
        return result
