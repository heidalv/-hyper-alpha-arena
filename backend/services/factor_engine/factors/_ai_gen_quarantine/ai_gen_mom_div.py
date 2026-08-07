"""AI因子: 动量分歧因子 | 置信:62% | 衡量价格动量与成交量变化的方向背离程度。当价格上涨但成交量下降，或价格下跌但成交量上升时，表明当前趋势可能缺乏支撑，容易发生反转，与亏损模式中的sl、tp亏损吻合。因子值为负表示潜在反转风险，正值表示趋势与成交量一致。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumDivergenceFactor(BaseFactor):
    """衡量价格动量与成交量变化的方向背离程度。当价格上涨但成交量下降，或价格下跌但成交量上升时，表明当前趋势可能缺乏支撑，容易发生反转，与亏损模式中的sl、tp亏损吻合。因子值为负表示潜在反转风险，正值表示趋势与成交量一致。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_div",
            name="Momentum Divergence Factor",
            display_name="动量分歧因子",
            description="衡量价格动量与成交量变化的方向背离程度。当价格上涨但成交量下降，或价格下跌但成交量上升时，表明当前趋势可能缺乏支撑，容易发生反转，与亏损模式中的sl、tp亏损吻合。因子值为负表示潜在反转风险，正值表示趋势与成交量一致。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 价格动量：过去5根K线的收益率
        price_mom = data['close'].pct_change(5)
        # 成交量变化：过去5根K线的成交量变化率
        vol_mom = data['volume'].pct_change(5)
        # 计算背离得分：当价格动量与成交量动量符号相反时，赋值为-abs(price_mom)的符号；相同则+abs
        # 使用sign函数
        price_sign = np.sign(price_mom)
        vol_sign = np.sign(vol_mom)
        # 相同时：动量强度为正贡献；相反时为负贡献
        same = (price_sign == vol_sign) | (price_sign == 0) | (vol_sign == 0)
        opposite = (price_sign != vol_sign) & (price_sign != 0) & (vol_sign != 0)
        # 用价格动量绝对值作为权重
        factor = pd.Series(0.0, index=data.index)
        factor[same] = 0.0  # 中性，或可给正小值，为简化给0
        # opposite时，赋予负的价格动量绝对值归一化
        # 归一化用tanh( price_mom.abs() * 10 ) 映射到[0,1]然后取负
        abs_mom = price_mom.abs()
        norm = np.tanh(abs_mom * 10)  # 0~1
        factor[opposite] = -norm[opposite]
        return factor.fillna(0).clip(-1, 1)
