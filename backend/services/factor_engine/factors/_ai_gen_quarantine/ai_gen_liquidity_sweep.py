"""AI因子: 流动性扫单检测 | 置信:60% | 通过计算价格相对日内高低的极端位置与成交量的异常放大，识别可能存在的流动性扫单（dust_cleanup）风险，值负表示高风险，正表示安全。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquiditySweepDetector(BaseFactor):
    """通过计算价格相对日内高低的极端位置与成交量的异常放大，识别可能存在的流动性扫单（dust_cleanup）风险，值负表示高风险，正表示安全。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liquidity_sweep",
            name="LiquiditySweepDetector",
            display_name="流动性扫单检测",
            description="通过计算价格相对日内高低的极端位置与成交量的异常放大，识别可能存在的流动性扫单（dust_cleanup）风险，值负表示高风险，正表示安全。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        # 计算日内价格位置： (close - low) / (high - low) 避免除以零
        range = data['high'] - data['low']
        position = (data['close'] - data['low']) / range.replace(0, 1e-10)
        # 计算成交量相对于过去20期均值的偏离（成交量放大倍数）
        vol_ma20 = data['volume'].rolling(20, min_periods=1).mean()
        vol_ratio = data['volume'] / vol_ma20.replace(0, 1e-10)
        # 极端位置（接近0或1）且成交量异常放大 => 扫单风险
        extreme_pos = (position < 0.1) | (position > 0.9)
        high_vol = vol_ratio > 2.0
        risk = extreme_pos & high_vol
        # 返回-1到+1，-1表示高风险扫单，+1表示无风险
        return -risk.astype(float) + (1 - risk.astype(float)) * 0.5
        # 简化：有风险返回-1，无风险返回0.5
        # 调整：return -risk.astype(float) * 1.0 + (~risk).astype(float) * 0.5
