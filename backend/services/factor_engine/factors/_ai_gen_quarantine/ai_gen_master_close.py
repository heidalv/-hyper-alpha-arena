"""AI因子: 主力平仓异常因子 | 置信:55% | 通过分析价格与成交量的背离以及大单净流量，识别主力大规模平仓导致的异常抛压或拉抬。当价格下跌而成交量激增且波动率上升时，因子接近-1（做多风险）；反之价格上升但量能不足以支撑，因子接近+1（做空风险）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MasterRunningClose(BaseFactor):
    """通过分析价格与成交量的背离以及大单净流量，识别主力大规模平仓导致的异常抛压或拉抬。当价格下跌而成交量激增且波动率上升时，因子接近-1（做多风险）；反之价格上升但量能不足以支撑，因子接近+1（做空风险）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_master_close",
            name="Master Running Close",
            display_name="主力平仓异常因子",
            description="通过分析价格与成交量的背离以及大单净流量，识别主力大规模平仓导致的异常抛压或拉抬。当价格下跌而成交量激增且波动率上升时，因子接近-1（做多风险）；反之价格上升但量能不足以支撑，因子接近+1（做空风险）。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        volume = data['volume']
        high = data['high']
        low = data['low']
        # 计算价格变动方向（1上涨，-1下跌）
        price_dir = np.sign(close.diff())
        # 成交量相对前期的倍数
        vol_ratio = volume / volume.rolling(10).mean()
        # 价格波动率（ATR比值）
        atr = (high - low).rolling(14).mean()
        atr_ratio = atr / atr.rolling(50).mean()
        # 异常信号：价格下跌（dir=-1）且成交量放大>1.5倍且波动率上升>1.2，则主力卖出信号（负）
        # 价格上涨（dir=1）且成交量萎缩<0.8倍且波动率下降，则纯拉升无支撑信号（正）
        sell_signal = (price_dir == -1) & (vol_ratio > 1.5) & (atr_ratio > 1.2)
        buy_signal = (price_dir == 1) & (vol_ratio < 0.8) & (atr_ratio < 0.8)
        result = pd.Series(0, index=close.index)
        result[sell_signal] = -1.0
        result[buy_signal] = 1.0
        # 使用4周期EMA平滑
        result = result.ewm(span=4, adjust=False).mean()
        return result
