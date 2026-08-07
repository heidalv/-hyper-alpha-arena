"""AI因子: 止损脆弱性 | 置信:65% | 预测价格触发止损的概率，基于近期波动率、反转信号和回撤深度。当价格接近近期低点且波动率上升时输出负值，表示容易被止损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Stop_Loss_Vulnerability(BaseFactor):
    """预测价格触发止损的概率，基于近期波动率、反转信号和回撤深度。当价格接近近期低点且波动率上升时输出负值，表示容易被止损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sl",
            name="Stop_Loss_Vulnerability",
            display_name="止损脆弱性",
            description="预测价格触发止损的概率，基于近期波动率、反转信号和回撤深度。当价格接近近期低点且波动率上升时输出负值，表示容易被止损。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算近期回撤
        max_close = close.rolling(20).max()
        drawdown = (max_close - close) / max_close * 100
        # 计算波动率
        returns = close.pct_change()
        volatility = returns.rolling(10).std() * np.sqrt(365) * 100
        # 计算反转强度：当前价格与过去N日均值的偏离
        ma = close.rolling(10).mean()
        deviation = (close - ma) / ma * 100
        # 组合：大回撤+高波动+价格低于均线 = 止损风险高
        norm_dd = np.clip(drawdown / 10, 0, 1)  # 10%为最大
        norm_vol = np.clip(volatility / 80, 0, 1)  # 年化80%为高
        norm_dev = np.clip(-deviation / 5, 0, 1)  # 低于均线5%为强信号
        sl_risk = (norm_dd * 0.4 + norm_vol * 0.3 + norm_dev * 0.3) * 2 - 1
        sl_risk = sl_risk.fillna(0)
        return sl_risk
