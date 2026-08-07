"""AI因子: 持仓超时风险指标 | 置信:60% | 检测市场处于低波动、低成交量盘整状态，模拟hold_timeout_review亏损模式。通过计算历史波动率下降至低位且成交量持续萎缩来预测未来可能出现的突发方向选择或流动性枯竭。返回-1表示高风险空头（可能下跌），+1表示高风险多头（可能上涨），0为中性。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldTimeoutRiskIndicator(BaseFactor):
    """检测市场处于低波动、低成交量盘整状态，模拟hold_timeout_review亏损模式。通过计算历史波动率下降至低位且成交量持续萎缩来预测未来可能出现的突发方向选择或流动性枯竭。返回-1表示高风险空头（可能下跌），+1表示高风险多头（可能上涨），0为中性。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_hold_timeout",
            name="Hold Timeout Risk Indicator",
            display_name="持仓超时风险指标",
            description="检测市场处于低波动、低成交量盘整状态，模拟hold_timeout_review亏损模式。通过计算历史波动率下降至低位且成交量持续萎缩来预测未来可能出现的突发方向选择或流动性枯竭。返回-1表示高风险空头（可能下跌），+1表示高风险多头（可能上涨），0为中性。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        # 历史波动率：过去20日真实波幅均值
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr20 = tr.rolling(20).mean()
        # 波动率相对位置：当前ATR与过去60日ATR均值比较
        atr60 = tr.rolling(60).mean()
        vol_ratio = atr20 / atr60
        # 成交量萎缩：当前成交量低于过去20日均值的50%
        vol_ma20 = volume.rolling(20).mean()
        vol_low = volume < (vol_ma20 * 0.5)
        # 盘整判定：波动率处于历史低位且成交量极低
        consolidation = (vol_ratio < 0.7) & vol_low
        # 预测方向：使用短期动量判断盘整后可能的方向（前5日收盘价变化）
        ret5 = close.pct_change(5)
        # 如果盘整且之前上涨，则可能向下突破（持仓超时风险）
        up_risk = consolidation & (ret5 > 0.02)
        down_risk = consolidation & (ret5 < -0.02)
        result = pd.Series(0, index=close.index)
        result[up_risk] = -1.0  # 盘整后向上突破风险，看空
        result[down_risk] = 1.0  # 盘整后向下突破风险，看多
        return result
