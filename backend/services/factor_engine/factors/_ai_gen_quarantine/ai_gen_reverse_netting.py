"""AI因子: 反转净额 | 置信:50% | 基于成交量方向变化捕捉反转。计算日内价格范围（高-低）与成交量比值，若比值突然放大且价格从低点反弹，暗示卖方耗尽。使用当前K线低点与前K线低点对比，结合成交量变化率，输出归一化信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReversalNetting(BaseFactor):
    """基于成交量方向变化捕捉反转。计算日内价格范围（高-低）与成交量比值，若比值突然放大且价格从低点反弹，暗示卖方耗尽。使用当前K线低点与前K线低点对比，结合成交量变化率，输出归一化信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reverse_netting",
            name="Reversal Netting",
            display_name="反转净额",
            description="基于成交量方向变化捕捉反转。计算日内价格范围（高-低）与成交量比值，若比值突然放大且价格从低点反弹，暗示卖方耗尽。使用当前K线低点与前K线低点对比，结合成交量变化率，输出归一化信号。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算价格范围
        spread = data['high'] - data['low']
        # 避免除零
        vol_safe = data['volume'].replace(0, 1e-6)
        ratio = spread / vol_safe
        # 与过去10日均值比较
        ma_ratio = ratio.rolling(window=10).mean()
        ratio_z = (ratio - ma_ratio) / ma_ratio.replace(0, 1e-6)
        # 价格低点是否在上升（反弹）
        low_increasing = data['low'] > data['low'].shift(1)
        # 条件：比值放大且低点上升
        condition = (ratio_z > 0.5) & low_increasing
        # 信号强度：使用相对强度
        strength = np.clip(ratio_z, 0, 2) / 2.0
        signal = np.where(condition, strength, -strength * 0.5)
        # 平滑
        signal = signal.rolling(window=3).mean().fillna(0)
        return pd.Series(signal, index=data.index)
