"""AI因子: 假突破识别器 | 置信:60% | 检测价格突破近期高点/低点但收盘未能站稳（突破幅度小于0.5%且收盘价回撤超过突破幅度的50%），同时成交量放大但后续波动率骤降。此类模式往往导致止损或超时亏损。因子输出-1表示假突破，1表示有效突破。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PseudoBreakoutScreener(BaseFactor):
    """检测价格突破近期高点/低点但收盘未能站稳（突破幅度小于0.5%且收盘价回撤超过突破幅度的50%），同时成交量放大但后续波动率骤降。此类模式往往导致止损或超时亏损。因子输出-1表示假突破，1表示有效突破。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pseudo_breakout",
            name="PseudoBreakoutScreener",
            display_name="假突破识别器",
            description="检测价格突破近期高点/低点但收盘未能站稳（突破幅度小于0.5%且收盘价回撤超过突破幅度的50%），同时成交量放大但后续波动率骤降。此类模式往往导致止损或超时亏损。因子输出-1表示假突破，1表示有效突破。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 近期最高低点（10周期）
        recent_high = data['high'].rolling(10).max().shift(1)
        recent_low = data['low'].rolling(10).min().shift(1)
        # 突破检测
        high_break = data['high'] > recent_high * 1.001  # 轻微突破
        low_break = data['low'] < recent_low * 0.999
        # 收盘回撤幅度
        retrace_high = (data['high'] - data['close']) / (data['high'] - recent_high)
        retrace_low = (data['close'] - data['low']) / (recent_low - data['low'])
        # 成交量确认
        vol_spike = data['volume'] > data['volume'].rolling(20).mean() * 1.5
        # 假突破条件：突破后收盘回撤超过50%且成交量放大
        fake_break = (high_break & (retrace_high > 0.5) & vol_spike) | (low_break & (retrace_low > 0.5) & vol_spike)
        # 有效突破：突破后收盘在极端附近且成交量放大
        valid_break = (high_break & (retrace_high < 0.2) & vol_spike) | (low_break & (retrace_low < 0.2) & vol_spike)
        signal = np.where(fake_break, -1, np.where(valid_break, 1, 0))
        return pd.Series(signal, index=data.index).fillna(0)
