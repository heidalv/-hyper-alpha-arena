"""AI因子: 异常反向净额反转 | 置信:60% | 基于开盘、收盘价差与成交量异常的关系，捕捉多空不平衡引发的反转。当收盘价低于开盘价且成交量显著高于均值时，表明空头压力集中，后续可能反向；反之亦然。通过价差方向与成交量比率结合，输出连续信号值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class AbnormalNettingReversal(BaseFactor):
    """基于开盘、收盘价差与成交量异常的关系，捕捉多空不平衡引发的反转。当收盘价低于开盘价且成交量显著高于均值时，表明空头压力集中，后续可能反向；反之亦然。通过价差方向与成交量比率结合，输出连续信号值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_anr",
            name="Abnormal Netting Reversal",
            display_name="异常反向净额反转",
            description="基于开盘、收盘价差与成交量异常的关系，捕捉多空不平衡引发的反转。当收盘价低于开盘价且成交量显著高于均值时，表明空头压力集中，后续可能反向；反之亦然。通过价差方向与成交量比率结合，输出连续信号值。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        open_ = data['open']
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        window = 20
        vol_ma = volume.rolling(window).mean().replace(0, np.nan).ffill()
        vol_ratio = volume / vol_ma
        spread = (close - open_) / (high - low + 1e-8)
        raw = - spread * vol_ratio
        result = pd.Series(np.tanh(raw), index=data.index)
        return result.fillna(0)
