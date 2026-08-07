"""AI因子: 动量质量因子 | 置信:65% | 评估近期价格运动的确定性。对过去10日的收益率序列进行线性回归，取R平方值（拟合优度）。R平方高表示趋势稳定，高动量质量；R平方低表示随机波动，动量不可靠。映射到[-1,1]，正值代表高质量动量，负值代表随机波动。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Momentum_Quality_Factor(BaseFactor):
    """评估近期价格运动的确定性。对过去10日的收益率序列进行线性回归，取R平方值（拟合优度）。R平方高表示趋势稳定，高动量质量；R平方低表示随机波动，动量不可靠。映射到[-1,1]，正值代表高质量动量，负值代表随机波动。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momqual",
            name="Momentum Quality Factor",
            display_name="动量质量因子",
            description="评估近期价格运动的确定性。对过去10日的收益率序列进行线性回归，取R平方值（拟合优度）。R平方高表示趋势稳定，高动量质量；R平方低表示随机波动，动量不可靠。映射到[-1,1]，正值代表高质量动量，负值代表随机波动。",
            category="statistical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ret = close.pct_change()
        window = 10
        # 计算每个滚动窗口内的线性回归R^2
        def r_squared(series):
            y = series.values
            x = np.arange(len(y))
            # 简单线性回归
            slope, intercept = np.polyfit(x, y, 1)
            y_pred = slope * x + intercept
            ss_res = np.sum((y - y_pred)**2)
            ss_tot = np.sum((y - np.mean(y))**2)
            if ss_tot == 0:
                return 0.0
            return 1 - ss_res / ss_tot
        # 应用滚动窗口，注意需要至少window个非空值
        r2 = ret.rolling(window, min_periods=window).apply(r_squared, raw=False)
        # 映射到[-1,1]：R^2范围[0,1]，乘以2减1得到[-1,1]
        result = r2 * 2 - 1
        # 对缺失值填充0（无数据时视为中性）
        result = result.fillna(0)
        return result
