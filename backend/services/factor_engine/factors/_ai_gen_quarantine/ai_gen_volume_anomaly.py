"""AI因子: 成交量异常指数 | 置信:60% | 衡量当前成交量相对于过去20日成交量均值的偏离程度，并结合价格方向。若价格上涨且成交量异常放大，可能为趋势启动信号；若价格下跌且成交量放大，可能为恐慌性抛售。该因子可帮助识别 regime=unknown 下的异常流动性行为，避免被错误信号误导。正值表示成交量异常放大且价格正向，负值表示反向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeAnomalyIndex(BaseFactor):
    """衡量当前成交量相对于过去20日成交量均值的偏离程度，并结合价格方向。若价格上涨且成交量异常放大，可能为趋势启动信号；若价格下跌且成交量放大，可能为恐慌性抛售。该因子可帮助识别 regime=unknown 下的异常流动性行为，避免被错误信号误导。正值表示成交量异常放大且价格正向，负值表示反向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_anomaly",
            name="Volume Anomaly Index",
            display_name="成交量异常指数",
            description="衡量当前成交量相对于过去20日成交量均值的偏离程度，并结合价格方向。若价格上涨且成交量异常放大，可能为趋势启动信号；若价格下跌且成交量放大，可能为恐慌性抛售。该因子可帮助识别 regime=unknown 下的异常流动性行为，避免被错误信号误导。正值表示成交量异常放大且价格正向，负值表示反向。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        volume = data['volume']
        close = data['close']
        vol_ma20 = volume.rolling(20).mean()
        vol_ratio = volume / vol_ma20
        # 标准化vol_ratio到[-1,1]：log处理
        vol_std = np.log(vol_ratio + 1e-10)
        # 价格方向：当前收盘与5日均线比较
        ma5 = close.rolling(5).mean()
        price_direction = np.sign(close - ma5)
        result = vol_std * price_direction
        result = result.clip(-1, 1)
        return result.fillna(0)
