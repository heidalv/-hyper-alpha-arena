"""AI因子: 量价背离因子 | 置信:65% | 捕捉成交量放大但价格未有效突破的异常状态，类似dust_cleanup和liq_magnet_reversal的失效模式。当成交量激增而价格窄幅波动或反向时，表明市场存在非理性交易或流动性陷阱。使用成交量异常和价格变动方向一致性。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDissonanceFactor(BaseFactor):
    """捕捉成交量放大但价格未有效突破的异常状态，类似dust_cleanup和liq_magnet_reversal的失效模式。当成交量激增而价格窄幅波动或反向时，表明市场存在非理性交易或流动性陷阱。使用成交量异常和价格变动方向一致性。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_price_dissonance",
            name="Volume-Price Dissonance Factor",
            display_name="量价背离因子",
            description="捕捉成交量放大但价格未有效突破的异常状态，类似dust_cleanup和liq_magnet_reversal的失效模式。当成交量激增而价格窄幅波动或反向时，表明市场存在非理性交易或流动性陷阱。使用成交量异常和价格变动方向一致性。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 成交量异常度：当前成交量相对过去20日均值的比率
        vol_ma20 = volume.rolling(20).mean().fillna(volume.expanding().mean())
        vol_ratio = volume / vol_ma20
        # 价格变动幅度（绝对值）相对ATR
        tr = np.maximum(data['high'] - data['low'], np.maximum(abs(data['high'] - close.shift(1)), abs(data['low'] - close.shift(1))))
        atr = tr.rolling(14).mean().fillna(tr.expanding().mean())
        price_move = abs(close - close.shift(1))
        normalized_move = price_move / (atr + 1e-8)
        # 量价背离：高成交量但低价格波动
        dissonance = vol_ratio / (normalized_move + 0.5)
        # 平滑并归一化
        z = (dissonance - dissonance.rolling(60).mean().fillna(1.0)) / dissonance.rolling(60).std().fillna(1.0)
        result = np.clip(z * 0.2, -1, 1)
        return result.rename('ai_gen_volume_price_dissonance')
