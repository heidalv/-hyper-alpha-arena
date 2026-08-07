"""AI因子: 市场状态质量 | 置信:65% | 通过比较短期波动率与趋势强度（ADX）来判断市场是否处于稳定趋势或未知噪声状态。当波动率较高而趋势不明显时，认为市场状态未知（低质量），返回负值；反之返回正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Market_Regime_Quality(BaseFactor):
    """通过比较短期波动率与趋势强度（ADX）来判断市场是否处于稳定趋势或未知噪声状态。当波动率较高而趋势不明显时，认为市场状态未知（低质量），返回负值；反之返回正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mrq",
            name="Market Regime Quality",
            display_name="市场状态质量",
            description="通过比较短期波动率与趋势强度（ADX）来判断市场是否处于稳定趋势或未知噪声状态。当波动率较高而趋势不明显时，认为市场状态未知（低质量），返回负值；反之返回正值。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        # ATR
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 短期波动率: 过去20天收盘价对数收益率标准差
        ret = np.log(close / close.shift(1))
        short_vol = ret.rolling(20).std()
        # ADX 简化: 使用 +DI 和 -DI 的差值绝对值
        period = 14
        up = high - high.shift(1)
        down = low.shift(1) - low
        plus_dm = np.where((up > down) & (up > 0), up, 0)
        minus_dm = np.where((down > up) & (down > 0), down, 0)
        tr_series = tr
        atr_s = tr_series.rolling(period).mean()
        plus_di = 100 * (pd.Series(plus_dm).rolling(period).mean() / atr_s)
        minus_di = 100 * (pd.Series(minus_dm).rolling(period).mean() / atr_s)
        dx = (plus_di - minus_di).abs() / (plus_di + minus_di) * 100
        adx = dx.rolling(period).mean()
        # 组合: 波动率比 (short_vol / (atr/close)) 和 adx
        vol_ratio = short_vol / (atr / close).replace(0, np.nan)
        # 当 vol_ratio 高且 adx 低时，认为状态未知
        score = -np.clip(vol_ratio / vol_ratio.rolling(120).mean(), 0, 10) * (1 - np.clip(adx/100, 0, 1))
        # 标准化到[-1,1]
        result = -np.tanh(score * 2)
        result = result.fillna(0)
        return result.rename('factor_mrq')
