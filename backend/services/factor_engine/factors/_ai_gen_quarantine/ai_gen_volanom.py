"""AI因子: 成交量异常 | 置信:55% | 检测当前成交量相对于过去中位数的异常程度，并结合价格变动方向。若放量下跌或放量滞涨则视为风险信号（负值），缩量平稳则正值。可识别regime=unknown下的异常流动性。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeAnomaly(BaseFactor):
    """检测当前成交量相对于过去中位数的异常程度，并结合价格变动方向。若放量下跌或放量滞涨则视为风险信号（负值），缩量平稳则正值。可识别regime=unknown下的异常流动性。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volanom",
            name="VolumeAnomaly",
            display_name="成交量异常",
            description="检测当前成交量相对于过去中位数的异常程度，并结合价格变动方向。若放量下跌或放量滞涨则视为风险信号（负值），缩量平稳则正值。可识别regime=unknown下的异常流动性。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        volume = data['volume']
        close = data['close']
        n = 20
        vol_med = volume.rolling(n).median()
        vol_ratio = volume / (vol_med + 1e-10)
        price_change = close.pct_change(10)
        # 如果放量且价格下跌或涨幅很小，则为负向
        # 缩量上涨或平稳为正
        anomaly = vol_ratio * (price_change - 0.005)  # 阈值调整
        result = np.tanh(anomaly * 5)  # 压缩到[-1,1]
        return result
