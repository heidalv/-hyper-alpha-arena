"""AI因子: 布林带均值回复风险因子 | 置信:55% | 基于布林带宽度和价格相对于中轨的位置，判断是否处于极端位置容易引发均值回复但风险极高。当价格远离中轨且带宽收缩时，表示趋势不稳定易反转。输出[-1,1]，负值表示极端位置且波动率低（均值回复高风险），正值表示中性安全。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Bollingermeanreversionrisk(BaseFactor):
    """基于布林带宽度和价格相对于中轨的位置，判断是否处于极端位置容易引发均值回复但风险极高。当价格远离中轨且带宽收缩时，表示趋势不稳定易反转。输出[-1,1]，负值表示极端位置且波动率低（均值回复高风险），正值表示中性安全。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bbmeanrev",
            name="BollingerMeanReversionRisk",
            display_name="布林带均值回复风险因子",
            description="基于布林带宽度和价格相对于中轨的位置，判断是否处于极端位置容易引发均值回复但风险极高。当价格远离中轨且带宽收缩时，表示趋势不稳定易反转。输出[-1,1]，负值表示极端位置且波动率低（均值回复高风险），正值表示中性安全。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        n = 20
        ma = close.rolling(n).mean()
        std = close.rolling(n).std()
        upper = ma + 2 * std
        lower = ma - 2 * std
        # 带宽
        bandwidth = (upper - lower) / ma
        # 价格位置Z-score
        z = (close - ma) / (std + 1e-10)
        # 风险信号：当z绝对值>1.5且带宽小于历史中位数时，认为高风险
        med_band = bandwidth.rolling(60).median()
        risk_cond = (z.abs() > 1.5) & (bandwidth < med_band)
        # 信号：负值表示高风险均值回复
        factor = -risk_cond.astype(float) * z.abs().clip(0, 2) / 2
        # 在没有风险时，给中性小正数
        factor = factor.fillna(0)
        return pd.Series(factor, index=data.index)
