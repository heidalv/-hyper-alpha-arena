"""AI因子: 市场状态不确定因子 | 置信:55% | 基于ADX和价格波动率，当ADX低于25且20日波动率处于历史40%-60%分位数时，认为市场处于无趋势的未知状态，该状态下long单易亏损。因子输出负值（-1）表示强不确定性风险，正值表示趋势明确。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Regime_Uncertainty_Indicator(BaseFactor):
    """基于ADX和价格波动率，当ADX低于25且20日波动率处于历史40%-60%分位数时，认为市场处于无趋势的未知状态，该状态下long单易亏损。因子输出负值（-1）表示强不确定性风险，正值表示趋势明确。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_uncertain",
            name="Regime Uncertainty Indicator",
            display_name="市场状态不确定因子",
            description="基于ADX和价格波动率，当ADX低于25且20日波动率处于历史40%-60%分位数时，认为市场处于无趋势的未知状态，该状态下long单易亏损。因子输出负值（-1）表示强不确定性风险，正值表示趋势明确。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算ADX
        high = data['high']
        low = data['low']
        close = data['close']
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr = tr.rolling(14).mean()
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        plus_di = 100 * pd.Series(plus_dm).rolling(14).mean() / atr
        minus_di = 100 * pd.Series(minus_dm).rolling(14).mean() / atr
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(14).mean()
        # 计算20日波动率
        ret = close.pct_change()
        vol_20 = ret.rolling(20).std() * np.sqrt(20)
        # 波动率分位数
        vol_rank = vol_20.rolling(60).apply(lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min() + 1e-10), raw=False)
        # 信号：ADX<25 且波动率在0.4-0.6分位 => 不确定性高
        uncertainty = (adx < 25) & (vol_rank >= 0.4) & (vol_rank <= 0.6)
        # 输出-1~1，不确定性高时为负，否则为正（趋势强度映射）
        result = -1.0 * uncertainty.astype(float) + (1.0 - uncertainty.astype(float)) * (adx / 50.0 - 0.5)
        result = result.clip(-1, 1)
        return result
