"""AI因子: 市场状态确定性指数 | 置信:55% | 通过滚动偏度和波动率一致性来估计当前市场状态的确定性。计算过去N天收盘价序列的偏度绝对值（衡量分布对称性，趋势市场偏度大）以及ATR与ADX的背离程度，组合后归一化到[-1,1]。正值表示确定性高（趋势明显或震荡模式稳定），负值表示不确定性高（regime unknown）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeCertaintyIndex(BaseFactor):
    """通过滚动偏度和波动率一致性来估计当前市场状态的确定性。计算过去N天收盘价序列的偏度绝对值（衡量分布对称性，趋势市场偏度大）以及ATR与ADX的背离程度，组合后归一化到[-1,1]。正值表示确定性高（趋势明显或震荡模式稳定），负值表示不确定性高（regime unknown）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_certainty",
            name="Regime Certainty Index",
            display_name="市场状态确定性指数",
            description="通过滚动偏度和波动率一致性来估计当前市场状态的确定性。计算过去N天收盘价序列的偏度绝对值（衡量分布对称性，趋势市场偏度大）以及ATR与ADX的背离程度，组合后归一化到[-1,1]。正值表示确定性高（趋势明显或震荡模式稳定），负值表示不确定性高（regime unknown）。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        n = 20
        close = data['close']
        high = data['high']
        low = data['low']

        # 滚动偏度（偏度绝对值）
        skew = close.rolling(n).skew().abs()
        # 滚动标准差
        std = close.rolling(n).std()
        # ATR
        tr = np.maximum(high - low, np.maximum((high - data['close'].shift(1)).abs(), (low - data['close'].shift(1)).abs()))
        atr = tr.rolling(n).mean()
        # ADX简化：用方向性指标（+DI - DI）的绝对值
        delta = close.diff()
        up = delta.where(delta > 0, 0)
        down = (-delta).where(delta < 0, 0)
        tr_sum = tr.rolling(n).sum()
        plus_di = 100 * up.rolling(n).sum() / tr_sum
        minus_di = 100 * down.rolling(n).sum() / tr_sum
        dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100
        adx = dx.rolling(n).mean()

        # 一致性指标：如果ATR和ADX都高，则趋势强，确定性高；如果ATR高但ADX低，则震荡不确定
        # 使用缩放后的组合
        atr_norm = (atr - atr.rolling(60).mean()) / atr.rolling(60).std()
        adx_norm = (adx - adx.rolling(60).mean()) / adx.rolling(60).std()
        certainty = np.tanh(0.5 * (adx_norm - 0.3 * atr_norm) + 0.5 * (skew - skew.rolling(60).mean()) / skew.rolling(60).std())

        result = certainty.fillna(0)
        return result.clip(-1, 1)
