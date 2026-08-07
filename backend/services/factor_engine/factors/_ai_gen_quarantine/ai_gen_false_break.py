"""AI因子: 假突破信号 | 置信:50% | 检测价格尝试突破近期波动区间但失败的模式。利用布林带宽度变化与价格位置：当价格突破上轨后迅速回落至上轨以内，且成交量放大，视为假突破。计算开盘价与前一周期收盘价的关系，结合成交量异常。输出[-1,1]，正值表示多头假突破风险（看跌），负值表示空头假突破风险（看涨）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Falsebreakout(BaseFactor):
    """检测价格尝试突破近期波动区间但失败的模式。利用布林带宽度变化与价格位置：当价格突破上轨后迅速回落至上轨以内，且成交量放大，视为假突破。计算开盘价与前一周期收盘价的关系，结合成交量异常。输出[-1,1]，正值表示多头假突破风险（看跌），负值表示空头假突破风险（看涨）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_false_break",
            name="FalseBreakout",
            display_name="假突破信号",
            description="检测价格尝试突破近期波动区间但失败的模式。利用布林带宽度变化与价格位置：当价格突破上轨后迅速回落至上轨以内，且成交量放大，视为假突破。计算开盘价与前一周期收盘价的关系，结合成交量异常。输出[-1,1]，正值表示多头假突破风险（看跌），负值表示空头假突破风险（看涨）。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 布林带20日2标准差
        ma20 = data['close'].rolling(20).mean()
        std20 = data['close'].rolling(20).std()
        upper = ma20 + 2 * std20
        lower = ma20 - 2 * std20
        # 当前价格是否刚从上下轨之外回归
        # 条件1：前一周期价格突破上轨（close>upper）且当前close<upper
        prev_close = data['close'].shift(1)
        prev_upper = upper.shift(1)
        prev_lower = lower.shift(1)
        # 多头假突破：前收盘>上轨，今收盘<上轨
        bull_fake = (prev_close > prev_upper) & (data['close'] < upper)
        # 空头假突破：前收盘<下轨，今收盘>下轨
        bear_fake = (prev_close < prev_lower) & (data['close'] > lower)
        # 成交量放大倍数
        vol_ma = data['volume'].rolling(20).mean()
        vol_ratio = data['volume'] / vol_ma
        # 仅当成交量放大时信号有效（>1.2倍）
        valid_vol = vol_ratio > 1.2
        # 输出：多头假突破为负（下跌风险），空头假突破为正（上涨风险）
        result = np.zeros(len(data))
        result[bull_fake & valid_vol] = -1.0
        result[bear_fake & valid_vol] = 1.0
        return result
