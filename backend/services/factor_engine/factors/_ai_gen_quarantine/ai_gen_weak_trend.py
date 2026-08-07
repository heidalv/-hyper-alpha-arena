"""AI因子: 弱势趋势衰减 | 置信:60% | 通过短期均线（5日）与长期均线（20日）的差距以及RSI的相对位置，识别持续下跌但动能衰减的弱势格局。此类格局下做多容易因趋势延续或震荡而触发止损。因子计算：均线差（MA5/MA20-1）乘以RSI(14)偏离50的程度（RSI-50），取负值后使用缩放函数映射至[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Weak_Trend_Decay(BaseFactor):
    """通过短期均线（5日）与长期均线（20日）的差距以及RSI的相对位置，识别持续下跌但动能衰减的弱势格局。此类格局下做多容易因趋势延续或震荡而触发止损。因子计算：均线差（MA5/MA20-1）乘以RSI(14)偏离50的程度（RSI-50），取负值后使用缩放函数映射至[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_weak_trend",
            name="Weak Trend Decay",
            display_name="弱势趋势衰减",
            description="通过短期均线（5日）与长期均线（20日）的差距以及RSI的相对位置，识别持续下跌但动能衰减的弱势格局。此类格局下做多容易因趋势延续或震荡而触发止损。因子计算：均线差（MA5/MA20-1）乘以RSI(14)偏离50的程度（RSI-50），取负值后使用缩放函数映射至[-1,1]。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 均线
        ma5 = data['close'].rolling(5).mean()
        ma20 = data['close'].rolling(20).mean()
        ma_diff = ma5 / ma20 - 1
        # RSI
        delta = data['close'].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        rsi_dev = (rsi - 50) / 50  # 范围约[-1,1]
        # 组合：均线差为负（死叉）且rsi低于50时，乘积为正，取负得负。
        raw = -ma_diff * rsi_dev
        # 由于ma_diff范围很小，先clip再乘以系数
        raw = raw.clip(-0.2, 0.2) * 10  # 放大到[-2,2]左右
        result = np.tanh(raw)
        return result.fillna(0)
