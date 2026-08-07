"""AI因子: 市场状态自适应反转 | 置信:65% | 结合ATR和ADX识别市场状态，当ADX低于阈值（趋势弱）且短期价格偏离均线较大时，认为处于'unknown' regime，产生反向信号。在强趋势时则跟随趋势。信号范围[-1,1]表示做空或做多强度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Regime_Adaptive_Trend_Reversal(BaseFactor):
    """结合ATR和ADX识别市场状态，当ADX低于阈值（趋势弱）且短期价格偏离均线较大时，认为处于'unknown' regime，产生反向信号。在强趋势时则跟随趋势。信号范围[-1,1]表示做空或做多强度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_shift",
            name="Regime Adaptive Trend Reversal",
            display_name="市场状态自适应反转",
            description="结合ATR和ADX识别市场状态，当ADX低于阈值（趋势弱）且短期价格偏离均线较大时，认为处于'unknown' regime，产生反向信号。在强趋势时则跟随趋势。信号范围[-1,1]表示做空或做多强度。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 参数
        adx_period = 14
        atr_period = 14
        ma_period = 20
        adx_threshold = 25
        zscore_threshold = 2.0

        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']

        # 计算ATR
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.rolling(atr_period).mean()

        # 计算ADX
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        tr_smooth = tr.rolling(atr_period).sum()
        plus_di = 100 * pd.Series(plus_dm).rolling(atr_period).sum() / tr_smooth
        minus_di = 100 * pd.Series(minus_dm).rolling(atr_period).sum() / tr_smooth
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        adx = dx.rolling(adx_period).mean()

        # 计算均线和zscore
        ma = close.rolling(ma_period).mean()
        std = close.rolling(ma_period).std()
        zscore = (close - ma) / (std + 1e-10)

        # 信号生成
        # 当ADX低且zscore超过阈值时，反向
        low_adx = adx < adx_threshold
        overbought = (zscore > zscore_threshold) & low_adx
        oversold = (zscore < -zscore_threshold) & low_adx

        # 当ADX高时，趋势跟随
        high_adx = adx >= adx_threshold
        trend_up = (close > ma) & high_adx
        trend_down = (close < ma) & high_adx

        signal = pd.Series(0.0, index=data.index)
        signal[overbought] = -1.0  # 做空
        signal[oversold] = 1.0     # 做多
        signal[trend_up] = 1.0
        signal[trend_down] = -1.0

        # 归一化到[-1,1]（已有）
        return signal
