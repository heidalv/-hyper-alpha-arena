"""AI因子: 趋势一致性混乱指标 | 置信:70% | 通过短、中、长期移动平均线的排列关系判断趋势是否一致。当短均线与中均线方向和中均线与长均线方向相反时，市场处于纠结状态（regime unknown），此时趋势不明确，做多易亏损，因子输出负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Coherence_Confusion(BaseFactor):
    """通过短、中、长期移动平均线的排列关系判断趋势是否一致。当短均线与中均线方向和中均线与长均线方向相反时，市场处于纠结状态（regime unknown），此时趋势不明确，做多易亏损，因子输出负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trendcoherence",
            name="Trend_Coherence_Confusion",
            display_name="趋势一致性混乱指标",
            description="通过短、中、长期移动平均线的排列关系判断趋势是否一致。当短均线与中均线方向和中均线与长均线方向相反时，市场处于纠结状态（regime unknown），此时趋势不明确，做多易亏损，因子输出负值。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        ma5 = close.rolling(5).mean()
        ma20 = close.rolling(20).mean()
        ma60 = close.rolling(60).mean()

        # 相对价格归一化差值
        diff1 = (ma5 - ma20) / close
        diff2 = (ma20 - ma60) / close

        # 判断符号
        sign1 = np.sign(diff1)
        sign2 = np.sign(diff2)
        same_sign = (sign1 == sign2) & (sign1 != 0)

        # 相同方向时，信号为平均斜率（正负表示趋势方向），范围约[-0.1,0.1]
        # 不同方向时，输出负向混乱强度
        strength = np.abs(diff1) + np.abs(diff2)
        factor = np.where(same_sign, (diff1 + diff2) / 2, -strength)
        # 使用tanh压缩到[-1,1]
        factor = np.tanh(factor * 20)  # 放大非线性
        factor = pd.Series(factor, index=close.index)
        factor = factor.fillna(0)
        return factor
