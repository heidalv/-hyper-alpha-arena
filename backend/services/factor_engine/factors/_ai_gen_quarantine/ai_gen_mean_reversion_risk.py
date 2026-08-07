"""AI因子: 均值回归风险因子 | 置信:55% | 基于短期相对强弱指标（RSI）与波动率的组合，检测市场处于极端超买或超卖状态，在未知市场状态下当波动率较高时，均值回归概率增大，提示逆势风险。该因子负值表示超买（可能回调），正值表示超卖（可能反弹）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Mean_Reversion_Risk(BaseFactor):
    """基于短期相对强弱指标（RSI）与波动率的组合，检测市场处于极端超买或超卖状态，在未知市场状态下当波动率较高时，均值回归概率增大，提示逆势风险。该因子负值表示超买（可能回调），正值表示超卖（可能反弹）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mean_reversion_risk",
            name="Mean Reversion Risk",
            display_name="均值回归风险因子",
            description="基于短期相对强弱指标（RSI）与波动率的组合，检测市场处于极端超买或超卖状态，在未知市场状态下当波动率较高时，均值回归概率增大，提示逆势风险。该因子负值表示超买（可能回调），正值表示超卖（可能反弹）。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        volume = data['volume']
        high = data['high']
        low = data['low']
        # 计算14周期RSI
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / (loss + 1e-10)
        rsi = 100 - 100 / (1 + rs)
        # 计算波动率（20周期标准差）
        atr = ((high - low).rolling(14).mean()) / close.rolling(14).mean() * 100
        # 标准化rsi到[-1,1]：超买>70 -> 负值，超卖<30 -> 正值
        rsi_signal = -((rsi - 50) / 30).clip(-1, 1)
        # 用波动率调制：高波动时信号更强
        vol_normalized = atr / atr.rolling(100).mean()
        result = rsi_signal * np.minimum(vol_normalized.fillna(1).clip(0.5, 2), 2.0)
        result = result.fillna(0).clip(-1, 1)
        return result
