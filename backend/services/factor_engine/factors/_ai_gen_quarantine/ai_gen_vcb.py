"""AI因子: 波动收缩假突破 | 置信:60% | 多次亏损表现为 max_hold_timeout，可能是低波动收缩后假突破，持仓迅速反转导致超时。该因子检测布林带宽度收缩至极低水平后的价格突破失败信号：若宽度为近期低位且价格反向刺穿布林带外轨，给出强烈反向预警（-1）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityContractionBreakoutFailure(BaseFactor):
    """多次亏损表现为 max_hold_timeout，可能是低波动收缩后假突破，持仓迅速反转导致超时。该因子检测布林带宽度收缩至极低水平后的价格突破失败信号：若宽度为近期低位且价格反向刺穿布林带外轨，给出强烈反向预警（-1）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vcb",
            name="Volatility Contraction Breakout Failure",
            display_name="波动收缩假突破",
            description="多次亏损表现为 max_hold_timeout，可能是低波动收缩后假突破，持仓迅速反转导致超时。该因子检测布林带宽度收缩至极低水平后的价格突破失败信号：若宽度为近期低位且价格反向刺穿布林带外轨，给出强烈反向预警（-1）。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        import pandas as pd
        import numpy as np

    def calculate(self, data):
        close = data['close']
        # 布林带(20,2)
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper = sma20 + 2 * std20
        lower = sma20 - 2 * std20
        bandwidth = (upper - lower) / sma20
        # 宽度处于20日最低10%区域
        bw_low = bandwidth.rolling(20).apply(lambda x: x[-1] <= np.percentile(x, 10))
        # 突破失败信号：宽度极低时，价格曾向上突破后今日回落至上轨之下，或向下突破后回升至下轨之上
        failed_breakout_up = bw_low & (close.shift(1) > upper.shift(1)) & (close < upper)
        failed_breakout_down = bw_low & (close.shift(1) < lower.shift(1)) & (close > lower)
        # 信号强度：-1 为强烈反转预警（假突破方向反向）
        signal = pd.Series(0.0, index=close.index)
        signal[failed_breakout_up] = -1.0  # 向上假突破后下跌，对多头不利
        signal[failed_breakout_down] = 1.0  # 向下假突破后上涨，对空头不利
        # 平滑处理，避免过于稀疏
        result = signal.rolling(3, min_periods=1).mean()
        result = result.clip(-1, 1)
        return result
