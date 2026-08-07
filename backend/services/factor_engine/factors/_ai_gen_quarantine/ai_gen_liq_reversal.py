"""AI因子: 流动性吸引反转 | 置信:60% | 检测价格快速突破近期极值但迅速回撤的流动性吸引反转模式。当价格超过前期高点/低点同时伴随成交量放大，随后迅速回落至突破前水平，则发出反转信号。负值表示看空（多头陷阱），正值表示看多（空头陷阱）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityMagnetReversal(BaseFactor):
    """检测价格快速突破近期极值但迅速回撤的流动性吸引反转模式。当价格超过前期高点/低点同时伴随成交量放大，随后迅速回落至突破前水平，则发出反转信号。负值表示看空（多头陷阱），正值表示看多（空头陷阱）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liq_reversal",
            name="Liquidity Magnet Reversal",
            display_name="流动性吸引反转",
            description="检测价格快速突破近期极值但迅速回撤的流动性吸引反转模式。当价格超过前期高点/低点同时伴随成交量放大，随后迅速回落至突破前水平，则发出反转信号。负值表示看空（多头陷阱），正值表示看多（空头陷阱）。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        # 计算近期极值（20周期）
        highest_20 = high.rolling(20).max()
        lowest_20 = low.rolling(20).min()
        avg_vol_20 = volume.rolling(20).mean()
        # 突破条件：收盘价突破近期极值，且成交量放大1.5倍
        breakout_up = (close > highest_20.shift(1)) & (volume > avg_vol_20 * 1.5)
        breakout_down = (close < lowest_20.shift(1)) & (volume > avg_vol_20 * 1.5)
        # 反转条件：突破后下一根K线收盘价回到突破前极值以内
        reversal_up = breakout_up.shift(1) & (close < highest_20.shift(2))
        reversal_down = breakout_down.shift(1) & (close > lowest_20.shift(2))
        # 量化信号：多头陷阱 -1，空头陷阱 +1
        signal = np.where(reversal_down, 1, np.where(reversal_up, -1, 0))
        return pd.Series(signal, index=data.index).clip(-1, 1)
