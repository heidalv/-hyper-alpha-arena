"""AI因子: Z分数均值回归信号 | 置信:60% | 计算价格相对移动均线及标准差的Z分数，并用tanh映射到[-1,1]；负Z（超卖）输出正值提示反弹，正Z（超买）输出负值提示回落，用于捕捉regime=unknown下的均值回归机会。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ZScoreMeanReversionSignal(BaseFactor):
    """计算价格相对移动均线及标准差的Z分数，并用tanh映射到[-1,1]；负Z（超卖）输出正值提示反弹，正Z（超买）输出负值提示回落，用于捕捉regime=unknown下的均值回归机会。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mr",
            name="Z-Score Mean Reversion Signal",
            display_name="Z分数均值回归信号",
            description="计算价格相对移动均线及标准差的Z分数，并用tanh映射到[-1,1]；负Z（超卖）输出正值提示反弹，正Z（超买）输出负值提示回落，用于捕捉regime=unknown下的均值回归机会。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        n = 20
        mean = data['close'].rolling(n).mean()
        std = data['close'].rolling(n).std()
        zscore = (data['close'] - mean) / std.replace(0, np.nan)
        result = -np.tanh(zscore).fillna(0)
        return result.clip(-1, 1)
