"""AI因子: 生命周期衰减动量 | 置信:45% | 从lifecycle_time_decay和master_running_close亏损中提炼，捕捉持仓时间过长后价格动能衰减的规律。使用短期动量与长期均线偏离度组合。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LifecycleDecayMomentum(BaseFactor):
    """从lifecycle_time_decay和master_running_close亏损中提炼，捕捉持仓时间过长后价格动能衰减的规律。使用短期动量与长期均线偏离度组合。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_lifecycle_decay",
            name="Lifecycle Decay Momentum",
            display_name="生命周期衰减动量",
            description="从lifecycle_time_decay和master_running_close亏损中提炼，捕捉持仓时间过长后价格动能衰减的规律。使用短期动量与长期均线偏离度组合。",
            category="behavioral",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_ma = data['close'].rolling(5).mean()
        long_ma = data['close'].rolling(30).mean()
        mom = data['close'].pct_change(10)
        result = ((short_ma - long_ma) / (long_ma + 1e-9) * 2 + mom).clip(-1, 1)
        return result
