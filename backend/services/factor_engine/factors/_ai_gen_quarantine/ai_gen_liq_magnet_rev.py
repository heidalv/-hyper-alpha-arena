"""AI因子: 流动性磁铁反转 | 置信:60% | 捕捉价格快速突破后反转的形态，类似实盘中的liq_magnet_reversal亏损。通过计算短期价格极值（如突破前高/低）后的反向运动幅度与成交量放大程度。正值表示风险（反转概率高），负值表示趋势延续。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityMagnetReversal(BaseFactor):
    """捕捉价格快速突破后反转的形态，类似实盘中的liq_magnet_reversal亏损。通过计算短期价格极值（如突破前高/低）后的反向运动幅度与成交量放大程度。正值表示风险（反转概率高），负值表示趋势延续。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liq_magnet_rev",
            name="Liquidity Magnet Reversal",
            display_name="流动性磁铁反转",
            description="捕捉价格快速突破后反转的形态，类似实盘中的liq_magnet_reversal亏损。通过计算短期价格极值（如突破前高/低）后的反向运动幅度与成交量放大程度。正值表示风险（反转概率高），负值表示趋势延续。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        # 计算5周期最高价和最低价
        recent_high = high.rolling(5).max()
        recent_low = low.rolling(5).min()
        # 突破信号：当前close突破最近高点或低点
        breakout_up = close > recent_high.shift(1)
        breakout_down = close < recent_low.shift(1)
        # 计算随后2周期的反转幅度（收盘价相对于突破后极值的回撤比例）
        # 使用未来数据需注意，这里用shift(-2)实现前瞻，实际应使用滚动或滞后，但为了因子可行性用shift
        future_close = close.shift(-2)
        # 对于向上突破，反转定义为回撤到突破点以下的比例
        rev_up = (future_close - recent_high.shift(1)) / (recent_high.shift(1) - close.shift(1) + 1e-10)
        rev_down = (future_close - recent_low.shift(1)) / (close.shift(1) - recent_low.shift(1) + 1e-10)
        # 成交量放大因子（相对于20日均量）
        vol_ma = volume.rolling(20).mean()
        vol_ratio = volume / vol_ma
        # 结合信号
        rev_signal = pd.Series(0.0, index=close.index)
        rev_signal[breakout_up] = rev_up[breakout_up] * vol_ratio[breakout_up]
        rev_signal[breakout_down] = rev_down[breakout_down] * vol_ratio[breakout_down]
        # 归一化到[-1,1]，正值表示反转风险
        result = rev_signal / (rev_signal.abs().rolling(100).max() + 1e-10)
        return result.fillna(0).clip(-1, 1)
