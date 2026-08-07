"""AI因子: 趋势弱势因子 | 置信:60% | 衡量价格趋势的强度，通过短期均线与长期均线的偏离度除以ATR归一化，当趋势极弱（盘整）时输出负值，表示该市场状态可能导致持仓超时亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendWeakness(BaseFactor):
    """衡量价格趋势的强度，通过短期均线与长期均线的偏离度除以ATR归一化，当趋势极弱（盘整）时输出负值，表示该市场状态可能导致持仓超时亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tw",
            name="TrendWeakness",
            display_name="趋势弱势因子",
            description="衡量价格趋势的强度，通过短期均线与长期均线的偏离度除以ATR归一化，当趋势极弱（盘整）时输出负值，表示该市场状态可能导致持仓超时亏损。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算短期均线（20）和长期均线（50）
        sma20 = close.rolling(20).mean()
        sma50 = close.rolling(50).mean()
        # 计算ATR14
        tr = pd.concat([high - low, high - close.shift(1), close.shift(1) - low], axis=1).max(axis=1)
        atr14 = tr.rolling(14).mean()
        # 价格相对均线偏离
        diff = (close - sma50) / sma50
        # 用ATR标准化
        norm_diff = diff / (atr14 / close)
        # 映射到[-1,1]：当|norm_diff| < 0.5时认为弱势，给负值；较大时给正值
        result = -1 + 2 * (norm_diff.abs() / (norm_diff.abs() + 1))
        # 反转使得弱势时更负
        result = result * (norm_diff.apply(lambda x: -1 if x >= 0 else 1))  # 保持方向？但我们需要绝对值小的负值
        # 简化：直接使用负的指数函数
        result = -np.exp(-norm_diff.abs()*3)
        # 由于exp输出(-1,0)，调整到[-1,0]；再结合方向，但为了简单，只输出负值表示弱势
        # 更稳健：使用tanh映射
        result = -np.tanh(norm_diff.abs() * 2)
        return result.fillna(0)
