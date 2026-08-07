"""AI因子: 布林带宽度Z分数 | 置信:60% | 计算20日布林带带宽（2倍标准差/收盘价），然后计算该带宽相对于自身20日历史均值的Z分数，并利用tanh函数将结果限制在[-1,1]。当带宽异常窄（Z分数为负）时返回负值，指示震荡或未知市场状态，避免做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Bollinger_Band_Width_Z_Score(BaseFactor):
    """计算20日布林带带宽（2倍标准差/收盘价），然后计算该带宽相对于自身20日历史均值的Z分数，并利用tanh函数将结果限制在[-1,1]。当带宽异常窄（Z分数为负）时返回负值，指示震荡或未知市场状态，避免做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bollwidth",
            name="Bollinger Band Width Z-Score",
            display_name="布林带宽度Z分数",
            description="计算20日布林带带宽（2倍标准差/收盘价），然后计算该带宽相对于自身20日历史均值的Z分数，并利用tanh函数将结果限制在[-1,1]。当带宽异常窄（Z分数为负）时返回负值，指示震荡或未知市场状态，避免做多。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        period = 20
        close = data['close']
        # 计算标准差
        std = close.rolling(period).std()
        ma = close.rolling(period).mean()
        # 布林带带宽（归一化）
        bandwidth = 2 * std / ma
        # 计算带宽的滚动均值和标准差
        bw_mean = bandwidth.rolling(period).mean()
        bw_std = bandwidth.rolling(period).std()
        # Z分数
        z = (bandwidth - bw_mean) / bw_std
        # 用tanh限制到[-1,1]
        result = np.tanh(z).fillna(0)
        return result
