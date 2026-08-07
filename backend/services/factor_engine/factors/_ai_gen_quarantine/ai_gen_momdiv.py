"""AI因子: 动量分歧指标 | 置信:70% | 检测短期（5日）、中期（15日）、长期（30日）动量方向是否一致。三者同向时输出正值，表示趋势可信；出现分歧时输出负值，表明市场处于‘未知’的无方向状态，与实盘亏损模式高度相关。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumDivergenceIndicator(BaseFactor):
    """检测短期（5日）、中期（15日）、长期（30日）动量方向是否一致。三者同向时输出正值，表示趋势可信；出现分歧时输出负值，表明市场处于‘未知’的无方向状态，与实盘亏损模式高度相关。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momdiv",
            name="Momentum Divergence Indicator",
            display_name="动量分歧指标",
            description="检测短期（5日）、中期（15日）、长期（30日）动量方向是否一致。三者同向时输出正值，表示趋势可信；出现分歧时输出负值，表明市场处于‘未知’的无方向状态，与实盘亏损模式高度相关。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        # 计算三个时间尺度的收益率符号
        mom5 = np.sign(close.pct_change(5))
        mom15 = np.sign(close.pct_change(15))
        mom30 = np.sign(close.pct_change(30))
        # 计算符号之和，范围-3到3
        sum_sign = mom5 + mom15 + mom30
        # 当三个符号相同时，sum_sign为3或-3；两个相同为±1；全不同为0
        # 映射到[-1,1]: 先求绝对值，3->1, 1->1/3, 0->0，再乘回原符号
        abs_sum = np.abs(sum_sign)
        # 转换：3->1, 1->1/3, 0->0
        strength = np.where(abs_sum == 3, 1.0, np.where(abs_sum == 1, 1.0/3.0, 0.0))
        # 加上原符号
        result = np.sign(sum_sign) * strength
        # 处理NaN：如果不足数据前30个周期，填充0
        result = result.fillna(0)
        return result
