"""AI因子: 时间衰减动量因子 | 置信:60% | 基于持仓时间超时亏损（max_hold_timeout）的观察，设计考虑时间衰减的动量因子。近期收益权重高，远期收益权重低，以捕捉短期趋势的可靠性。如果近期收益为正但远期收益为负，表明趋势可能衰竭，因子值偏负。使用指数加权移动平均（halflife=5）计算收益，乘以价格方向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TimeDecayMomentum(BaseFactor):
    """基于持仓时间超时亏损（max_hold_timeout）的观察，设计考虑时间衰减的动量因子。近期收益权重高，远期收益权重低，以捕捉短期趋势的可靠性。如果近期收益为正但远期收益为负，表明趋势可能衰竭，因子值偏负。使用指数加权移动平均（halflife=5）计算收益，乘以价格方向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tau_mom",
            name="Time-Decay Momentum",
            display_name="时间衰减动量因子",
            description="基于持仓时间超时亏损（max_hold_timeout）的观察，设计考虑时间衰减的动量因子。近期收益权重高，远期收益权重低，以捕捉短期趋势的可靠性。如果近期收益为正但远期收益为负，表明趋势可能衰竭，因子值偏负。使用指数加权移动平均（halflife=5）计算收益，乘以价格方向。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算对数收益
        returns = np.log(data['close'] / data['close'].shift(1))
        # 指数加权移动平均，半衰期5期
        ewma = returns.ewm(halflife=5, adjust=False).mean()
        # 将ewma映射到[-1,1]，使用tanh缩放
        std = returns.rolling(20).std()
        norm_ewma = ewma / (std + 1e-10)
        result = np.tanh(3 * norm_ewma)  # 压缩范围
        result = result.fillna(0.0)
        return result.clip(-1, 1)
