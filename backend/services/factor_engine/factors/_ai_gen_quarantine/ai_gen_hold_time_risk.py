"""AI因子: 持仓时间风险因子 | 置信:60% | 基于ATR（平均真实波幅）和价格变化速率，衡量市场是否适合长周期持有。当价格在单位时间内的变动幅度小于ATR的一定倍数且波动率下降时，因子为负值，提示市场趋于平淡，持仓时间过长容易导致亏损（如hold_timeout_review）。正值表示波动活跃、趋势延续性较好，适合持仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldTimeRiskIndicator(BaseFactor):
    """基于ATR（平均真实波幅）和价格变化速率，衡量市场是否适合长周期持有。当价格在单位时间内的变动幅度小于ATR的一定倍数且波动率下降时，因子为负值，提示市场趋于平淡，持仓时间过长容易导致亏损（如hold_timeout_review）。正值表示波动活跃、趋势延续性较好，适合持仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_hold_time_risk",
            name="Hold Time Risk Indicator",
            display_name="持仓时间风险因子",
            description="基于ATR（平均真实波幅）和价格变化速率，衡量市场是否适合长周期持有。当价格在单位时间内的变动幅度小于ATR的一定倍数且波动率下降时，因子为负值，提示市场趋于平淡，持仓时间过长容易导致亏损（如hold_timeout_review）。正值表示波动活跃、趋势延续性较好，适合持仓。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        # ATR(10)
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.rolling(10).mean()
        # 价格变化率（过去5根K线收盘变化百分比）
        price_change = close.pct_change(periods=5).abs()
        # 波动率趋势：过去10日ATR与过去50日ATR的比值
        atr10 = atr
        atr50 = tr.rolling(50).mean()
        vol_trend = atr10 / (atr50 + 1e-10)
        # 风险指标：价格变化率大且波动扩大 -> 适合持仓；否则不适合
        risk_score = price_change * vol_trend
        # 归一化到[-1,1]，用历史百分位（简化用当前值减去0.5再缩放）
        result = pd.Series( 2 * (risk_score.rolling(100).rank(pct=True) - 0.5), index=close.index )
        return result.fillna(0)
