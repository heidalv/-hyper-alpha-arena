"""AI因子: 反转强度指标 | 置信:60% | 结合短期动量（ROC）和RSI的背离信号，当价格创新低而RSI未创新低（底背离）时预测向上反转，反之顶背离预测向下反转。因子值从-1（强下跌反转）到+1（强上涨反转），在背离发生时输出明显信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReversalStrengthIndicator(BaseFactor):
    """结合短期动量（ROC）和RSI的背离信号，当价格创新低而RSI未创新低（底背离）时预测向上反转，反之顶背离预测向下反转。因子值从-1（强下跌反转）到+1（强上涨反转），在背离发生时输出明显信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reversal_strength",
            name="Reversal_Strength_Indicator",
            display_name="反转强度指标",
            description="结合短期动量（ROC）和RSI的背离信号，当价格创新低而RSI未创新低（底背离）时预测向上反转，反之顶背离预测向下反转。因子值从-1（强下跌反转）到+1（强上涨反转），在背离发生时输出明显信号。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算ROC和RSI
        roc = data['close'].pct_change(5)
        delta = data['close'].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        # 计算价格和RSI的极值
        price_high = data['close'].rolling(10).max()
        price_low = data['close'].rolling(10).min()
        rsi_high = rsi.rolling(10).max()
        rsi_low = rsi.rolling(10).min()
        # 检测背离：当前价格接近10日低点但RSI未创新低->底背离（看涨）
        bearish_div = (data['close'] >= price_high) & (rsi <= rsi_low)  # 顶背离
        bullish_div = (data['close'] <= price_low) & (rsi >= rsi_high)  # 底背离
        # 基础信号来自ROC方向，结合背离增强
        base = -np.sign(roc)  # 当ROC负时base为正（看涨），反之看跌
        # 背离时增强幅度
        div_signal = np.where(bullish_div, 1.0, np.where(bearish_div, -1.0, 0.0))
        factor = base * 0.5 + div_signal * 0.5
        factor = np.clip(factor, -1, 1)
        return pd.Series(factor.fillna(0), index=data.index)
