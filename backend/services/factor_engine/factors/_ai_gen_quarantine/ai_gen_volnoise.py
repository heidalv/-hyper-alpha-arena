"""AI因子: 波动噪声比 | 置信:70% | 计算短期波动率与长期波动率的比值，结合价格方向变化的频率，识别市场处于趋势还是噪声状态。高比值且价格方向变化频繁时，噪声大，因子值接近-1；低比值且方向稳定时，趋势明确，因子值接近+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Noise_Ratio(BaseFactor):
    """计算短期波动率与长期波动率的比值，结合价格方向变化的频率，识别市场处于趋势还是噪声状态。高比值且价格方向变化频繁时，噪声大，因子值接近-1；低比值且方向稳定时，趋势明确，因子值接近+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volnoise",
            name="Volatility Noise Ratio",
            display_name="波动噪声比",
            description="计算短期波动率与长期波动率的比值，结合价格方向变化的频率，识别市场处于趋势还是噪声状态。高比值且价格方向变化频繁时，噪声大，因子值接近-1；低比值且方向稳定时，趋势明确，因子值接近+1。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close'].values
        returns = np.diff(close) / close[:-1]
        # 短期波动率（5周期）
        short_vol = pd.Series(returns).rolling(5).std().fillna(method='bfill').values
        # 长期波动率（20周期）
        long_vol = pd.Series(returns).rolling(20).std().fillna(method='bfill').values
        # 方向变化频率：统计5期内符号反转次数
        sign = np.sign(returns)
        sign_change = np.abs(np.diff(np.concatenate([[0], sign])))
        # 滚动求和
        freq = pd.Series(sign_change).rolling(5).sum().fillna(0).values
        # 合成噪声因子
        vol_ratio = short_vol / (long_vol + 1e-8)
        noise = vol_ratio * freq / 5.0
        # 将noise映射到[-1,1]：高噪声接近-1，低噪声接近+1
        result = 1 - 2 * np.clip(noise / (noise.max() + 1e-8), 0, 1)
        # 补全缺失
        full_result = np.concatenate([[0], result])
        return pd.Series(full_result).fillna(0).clip(-1, 1)
