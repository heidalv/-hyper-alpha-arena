"""AI因子: 布林带假突破压力 | 置信:60% | 衡量价格处于布林带边缘且带宽较窄时的假突破风险。当价格靠近上轨或下轨且带宽处于低位时，容易出现快速反转导致止损。因子值越高，风险越大。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class BollingerSqueezeFakeoutRisk(BaseFactor):
    """衡量价格处于布林带边缘且带宽较窄时的假突破风险。当价格靠近上轨或下轨且带宽处于低位时，容易出现快速反转导致止损。因子值越高，风险越大。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bsq",
            name="Bollinger Squeeze Fakeout Risk",
            display_name="布林带假突破压力",
            description="衡量价格处于布林带边缘且带宽较窄时的假突破风险。当价格靠近上轨或下轨且带宽处于低位时，容易出现快速反转导致止损。因子值越高，风险越大。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 布林带参数
        period = 20
        std_mult = 2.0
        rolling_mean = data['close'].rolling(period).mean()
        rolling_std = data['close'].rolling(period).std()
        upper = rolling_mean + std_mult * rolling_std
        lower = rolling_mean - std_mult * rolling_std
        bandwidth = (upper - lower) / rolling_mean
        price_position = (data['close'] - lower) / (upper - lower)
        # 远离中心的程度: 0到0.5之间(靠近边缘0或1时接近0.5)
        dist_from_center = 0.5 - (price_position - 0.5).abs()
        # 带宽归一化: 当前带宽相对于历史带宽的百分位（逆序）
        bandwidth_norm = bandwidth.rolling(period).rank(pct=True)
        # 因子 = dist_from_center * (1 - bandwidth_norm)，再映射到-1~1
        raw = dist_from_center * (1 - bandwidth_norm)
        result = (raw - raw.rolling(period).mean()) / (raw.rolling(period).std() + 1e-10)
        return result.clip(-1, 1).fillna(0)
