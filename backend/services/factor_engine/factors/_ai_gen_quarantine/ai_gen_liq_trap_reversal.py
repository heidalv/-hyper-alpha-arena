"""AI因子: 流动性陷阱反转 | 置信:65% | 检测价格快速向一个方向突破（通常伴随成交量放大）后迅速反转的形态，用于捕捉空头陷阱或多头陷阱。使用最近N根K线的最高价、最低价和成交量变化率，计算反转强度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityTrapReversal(BaseFactor):
    """检测价格快速向一个方向突破（通常伴随成交量放大）后迅速反转的形态，用于捕捉空头陷阱或多头陷阱。使用最近N根K线的最高价、最低价和成交量变化率，计算反转强度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liq_trap_reversal",
            name="Liquidity Trap Reversal",
            display_name="流动性陷阱反转",
            description="检测价格快速向一个方向突破（通常伴随成交量放大）后迅速反转的形态，用于捕捉空头陷阱或多头陷阱。使用最近N根K线的最高价、最低价和成交量变化率，计算反转强度。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: pd.DataFrame with columns ['open','high','low','close','volume']
        N = 5  # 窗口大小
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        # 计算最近N根K线的最高价和最低价
        recent_high = high.rolling(N).max()
        recent_low = low.rolling(N).min()
        # 计算价格相对于区间的百分比位置
        pos = (close - recent_low) / (recent_high - recent_low + 1e-10)
        # 成交量变化：当前成交量与过去N均值比值
        vol_ma = volume.rolling(N).mean()
        vol_ratio = volume / (vol_ma + 1e-10)
        # 反转信号：当价格靠近区间极值且成交量放大时，预期反转
        # 在顶部（pos>0.9）且成交量放大 => 看空反转信号（负值）
        # 在底部（pos<0.1）且成交量放大 => 看多反转信号（正值）
        signal = - (pos - 0.5) * 2 * (vol_ratio - 1)  # 动态缩放
        # 限制在[-1,1]
        signal = signal.clip(-1, 1)
        # 处理NaN
        signal = signal.fillna(0)
        return signal
