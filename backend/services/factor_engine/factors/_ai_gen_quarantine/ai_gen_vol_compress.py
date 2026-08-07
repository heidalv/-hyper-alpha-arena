"""AI因子: 波动率压缩陷阱 | 置信:55% | 当价格波动率（ATR/收盘价）显著降低至历史低位时，市场容易陷入窄幅震荡，导致多头持仓超时或缓慢亏损。该因子通过计算当前ATR与最近20日平均ATR的比率，当比率低于0.7时发出空头信号（-1），否则为0。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Compression_Trap(BaseFactor):
    """当价格波动率（ATR/收盘价）显著降低至历史低位时，市场容易陷入窄幅震荡，导致多头持仓超时或缓慢亏损。该因子通过计算当前ATR与最近20日平均ATR的比率，当比率低于0.7时发出空头信号（-1），否则为0。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_compress",
            name="Volatility Compression Trap",
            display_name="波动率压缩陷阱",
            description="当价格波动率（ATR/收盘价）显著降低至历史低位时，市场容易陷入窄幅震荡，导致多头持仓超时或缓慢亏损。该因子通过计算当前ATR与最近20日平均ATR的比率，当比率低于0.7时发出空头信号（-1），否则为0。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算ATR
        high = data['high']
        low = data['low']
        close = data['close']
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 归一化ATR相对于价格
        atr_pct = atr / close
        # 计算20日平均atr_pct
        avg_atr_pct = atr_pct.rolling(20).mean()
        ratio = atr_pct / avg_atr_pct
        # 当ratio<0.7时，认为波动压缩严重，发出-1信号，否则0
        result = pd.Series(np.where(ratio < 0.7, -1.0, 0.0), index=data.index)
        return result
