"""AI因子: 趋势置信度因子 | 置信:55% | 利用线性回归的R平方衡量短期趋势的明确性。当R平方值低（趋势模糊）时，持有或反向操作容易触发止损或超时亏损；反之高R平方时趋势可信。计算过去20周期收盘价的线性回归斜率与R方，通过R方与斜率方向组合生成信号：强上升趋势(+1)，强下降趋势(-1)，弱趋势接近0。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendConfidenceQuotient(BaseFactor):
    """利用线性回归的R平方衡量短期趋势的明确性。当R平方值低（趋势模糊）时，持有或反向操作容易触发止损或超时亏损；反之高R平方时趋势可信。计算过去20周期收盘价的线性回归斜率与R方，通过R方与斜率方向组合生成信号：强上升趋势(+1)，强下降趋势(-1)，弱趋势接近0。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tcq",
            name="Trend Confidence Quotient",
            display_name="趋势置信度因子",
            description="利用线性回归的R平方衡量短期趋势的明确性。当R平方值低（趋势模糊）时，持有或反向操作容易触发止损或超时亏损；反之高R平方时趋势可信。计算过去20周期收盘价的线性回归斜率与R方，通过R方与斜率方向组合生成信号：强上升趋势(+1)，强下降趋势(-1)，弱趋势接近0。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        window = 20
        def reg_rsq(series):
            x = np.arange(window)
            y = series.values
            if len(y) < window or np.isnan(y).any():
                return 0.0
            A = np.vstack([x, np.ones(window)]).T
            coeff, res, _, _ = np.linalg.lstsq(A, y, rcond=None)
            slope = coeff[0]
            y_pred = A.dot(coeff)
            ss_res = np.sum((y - y_pred)**2)
            ss_tot = np.sum((y - np.mean(y))**2)
            rsq = 1 - ss_res / (ss_tot + 1e-10)
            return slope * rsq  # 方向加权
        result = close.rolling(window).apply(reg_rsq, raw=False)
        # 标准化到[-1,1]
        result = np.clip(result / (np.std(close) * 0.1), -1, 1)
        return result.fillna(0.0)
