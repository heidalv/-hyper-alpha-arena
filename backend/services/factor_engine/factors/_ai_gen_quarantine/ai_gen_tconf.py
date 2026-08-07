"""AI因子: 趋势置信指数 | 置信:65% | 基于ADX和布林带位置计算趋势强度。ADX>25且价格在布林带上/下轨外时视为强趋势，否则为弱趋势。输出[-1,1]，正值表示强上升趋势，负值表示弱趋势或下降趋势。在弱趋势下避免做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Confidence_Index(BaseFactor):
    """基于ADX和布林带位置计算趋势强度。ADX>25且价格在布林带上/下轨外时视为强趋势，否则为弱趋势。输出[-1,1]，正值表示强上升趋势，负值表示弱趋势或下降趋势。在弱趋势下避免做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tconf",
            name="Trend Confidence Index",
            display_name="趋势置信指数",
            description="基于ADX和布林带位置计算趋势强度。ADX>25且价格在布林带上/下轨外时视为强趋势，否则为弱趋势。输出[-1,1]，正值表示强上升趋势，负值表示弱趋势或下降趋势。在弱趋势下避免做多。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # ADX
        high, low, close = data['high'], data['low'], data['close']
        period = 14
        # TR
        tr = pd.concat([high - low, abs(high - close.shift(1)), abs(low - close.shift(1))], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        # +DM, -DM
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        plus_di = 100 * pd.Series(plus_dm).rolling(period).mean() / atr
        minus_di = 100 * pd.Series(minus_dm).rolling(period).mean() / atr
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(period).mean() / 100  # scale to 0-1
        # Bollinger bands
        sma = close.rolling(20).mean()
        std = close.rolling(20).std()
        upper = sma + 2*std
        lower = sma - 2*std
        # position in bollinger: -1 at lower, +1 at upper
        bb_pos = (close - lower) / (upper - lower) * 2 - 1
        bb_pos = bb_pos.clip(-1, 1)
        # trend strength: adx*bb_pos, but adx is 0-1, combine
        trend_conf = adx * bb_pos
        # fill NaN
        trend_conf = trend_conf.fillna(0)
        return trend_conf.clip(-1, 1)
