"""AI因子: 量价背离 | 置信:60% | 检测成交量与价格走势的背离。计算价格短期变化率与成交量变化率的符号差异。当价格上涨但成交量萎缩（负背离）或价格下跌但成交量放大（正背离）时，可能预示趋势衰竭或反转，市场状态不确定，此时因子输出接近0；当量价同向时输出较强方向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """检测成交量与价格走势的背离。计算价格短期变化率与成交量变化率的符号差异。当价格上涨但成交量萎缩（负背离）或价格下跌但成交量放大（正背离）时，可能预示趋势衰竭或反转，市场状态不确定，此时因子输出接近0；当量价同向时输出较强方向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_divergence",
            name="Volume-Price Divergence",
            display_name="量价背离",
            description="检测成交量与价格走势的背离。计算价格短期变化率与成交量变化率的符号差异。当价格上涨但成交量萎缩（负背离）或价格下跌但成交量放大（正背离）时，可能预示趋势衰竭或反转，市场状态不确定，此时因子输出接近0；当量价同向时输出较强方向信号。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        # 价格变化率（5日）
        price_ret = close.pct_change(5)
        # 成交量变化率（5日均值）
        vol_ma = volume.rolling(window=5, min_periods=5).mean()
        vol_change = vol_ma.pct_change(5)
        # 计算量价同步性
        price_sign = np.sign(price_ret)
        vol_sign = np.sign(vol_change)
        # 同向时强度高，反向（背离）时强度低
        strength = np.where(price_sign == vol_sign, 1.0, -0.5)  # 背离时负值但不是-1，因为可能只是弱
        # 但实际背离时往往风险高，我们想让因子输出接近0，所以用价格方向乘以一个置信度
        # 更合理的：用价格变化大小和成交量变化大小加权
        price_mag = price_ret.abs() * 100  # 百分比
        vol_mag = vol_change.abs() * 100
        # 当量价同向时，取价格方向，否则取0
        direction = np.where(price_sign == vol_sign, price_sign, 0.0)
        # 使用价格幅度调整，避免噪声
        magnitude = np.minimum(price_mag, 3.0) / 3.0  # 归一化到0~1
        # 如果是背离，我们抑制信号
        result = direction * magnitude
        # 补充：如果成交量极端异常，可能预示反转，但这里简化
        result = result.fillna(0.0)
        result = result.clip(-1, 1)
        return result
