"""AI因子: 流动性缺口检测 | 置信:60% | 基于价格序列中连续两个收盘价之间的跳空幅度与成交量衰减程度，识别流动性不足导致的潜在亏损场景。当价格出现大幅跳空而成交量远低于均值时，容易形成不稳定的缺口，后续价格可能迅速回补或反向运动。因子计算滚动窗口内的跳空率与成交量异常，输出负值表示流动性恶化风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityGapDetector(BaseFactor):
    """基于价格序列中连续两个收盘价之间的跳空幅度与成交量衰减程度，识别流动性不足导致的潜在亏损场景。当价格出现大幅跳空而成交量远低于均值时，容易形成不稳定的缺口，后续价格可能迅速回补或反向运动。因子计算滚动窗口内的跳空率与成交量异常，输出负值表示流动性恶化风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liquidity_gap",
            name="Liquidity Gap Detector",
            display_name="流动性缺口检测",
            description="基于价格序列中连续两个收盘价之间的跳空幅度与成交量衰减程度，识别流动性不足导致的潜在亏损场景。当价格出现大幅跳空而成交量远低于均值时，容易形成不稳定的缺口，后续价格可能迅速回补或反向运动。因子计算滚动窗口内的跳空率与成交量异常，输出负值表示流动性恶化风险。",
            category="derivatives",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        volume = data['volume']
    
        # 计算跳空：当前收盘与前一收盘的绝对变化率
        gap = (close - close.shift(1)).abs() / close.shift(1)
        # 成交量相对均值偏离：当前成交量与过去20日均值之比
        vol_ma20 = volume.rolling(20).mean()
        vol_ratio = volume / vol_ma20
        # 定义流动性缺口：当gap大于阈值且vol_ratio小于阈值
        # 使用z-score标准化
        gap_z = (gap - gap.rolling(20).mean()) / gap.rolling(20).std()
        vol_z = (vol_ratio - vol_ratio.rolling(20).mean()) / vol_ratio.rolling(20).std()
        # 组合：gap异常大（正z）且成交量异常小（负z） => 流动性缺口
        raw = gap_z * (-vol_z)
        # 归一化到[-1,1]
        raw = raw.clip(-5, 5) / 5.0
        return raw.fillna(0)
