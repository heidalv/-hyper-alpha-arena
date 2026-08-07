"""AI因子: 市场清晰度因子 | 置信:70% | 通过ADX与ATR稳定性衡量市场趋势清晰程度，避免在regime=unknown的混沌状态下开仓。当ADX低于20且ATR波动剧烈时输出负值（表示不清晰），反之输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MarketClarityFactor(BaseFactor):
    """通过ADX与ATR稳定性衡量市场趋势清晰程度，避免在regime=unknown的混沌状态下开仓。当ADX低于20且ATR波动剧烈时输出负值（表示不清晰），反之输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_clarity",
            name="Market Clarity Factor",
            display_name="市场清晰度因子",
            description="通过ADX与ATR稳定性衡量市场趋势清晰程度，避免在regime=unknown的混沌状态下开仓。当ADX低于20且ATR波动剧烈时输出负值（表示不清晰），反之输出正值。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算ADX
        high, low, close = data['high'], data['low'], data['close']
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift()), abs(low - close.shift())))
        atr = tr.rolling(14).mean()
        # 方向移动
        up = high - high.shift()
        down = low.shift() - low
        pos_dm = np.where((up > down) & (up > 0), up, 0)
        neg_dm = np.where((down > up) & (down > 0), down, 0)
        tr14 = atr  # 使用ATR作为真实波幅
        pos_di = 100 * pd.Series(pos_dm, index=data.index).rolling(14).mean() / tr14
        neg_di = 100 * pd.Series(neg_dm, index=data.index).rolling(14).mean() / tr14
        dx = 100 * np.abs(pos_di - neg_di) / (pos_di + neg_di + 1e-10)
        adx = dx.rolling(14).mean()
        # ATR稳定性：近期ATR标准差 / 均值
        atr_std = atr.rolling(20).std()
        atr_mean = atr.rolling(20).mean()
        atr_stability = 1 - np.clip(atr_std / (atr_mean + 1e-10), 0, 2)  # 0~1，越小越稳定
        # 组合：ADX高于25且稳定则正，否则负
        adx_signal = (adx - 20) / 20.0
        stability_signal = (atr_stability - 0.5) * 2  # 映射到-1~1
        combined = 0.6 * np.clip(adx_signal, -1, 1) + 0.4 * np.clip(stability_signal, -1, 1)
        result = pd.Series(np.clip(combined, -1, 1), index=data.index)
        return result
