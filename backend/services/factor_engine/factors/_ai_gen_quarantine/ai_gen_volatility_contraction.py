"""AI因子: 波动率收缩突破风险 | 置信:55% | 利用ATR近期下降比例和价格在布林带内的带宽位置，识别窄幅震荡后的方向性突破风险。当ATR显著收缩且价格处于布林带中轨附近时，后续容易出现假突破导致反转。因子正值表示预期向上突破（对空头不利），负值表示向下突破（对多头不利）。适用于'hold_timeout_review'和'reverse_netting'等模式。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityContractionBreakoutRisk(BaseFactor):
    """利用ATR近期下降比例和价格在布林带内的带宽位置，识别窄幅震荡后的方向性突破风险。当ATR显著收缩且价格处于布林带中轨附近时，后续容易出现假突破导致反转。因子正值表示预期向上突破（对空头不利），负值表示向下突破（对多头不利）。适用于'hold_timeout_review'和'reverse_netting'等模式。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_contraction",
            name="Volatility Contraction Breakout Risk",
            display_name="波动率收缩突破风险",
            description="利用ATR近期下降比例和价格在布林带内的带宽位置，识别窄幅震荡后的方向性突破风险。当ATR显著收缩且价格处于布林带中轨附近时，后续容易出现假突破导致反转。因子正值表示预期向上突破（对空头不利），负值表示向下突破（对多头不利）。适用于'hold_timeout_review'和'reverse_netting'等模式。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        window = 20
        # ATR
        tr = pd.concat([df['high'] - df['low'], abs(df['high'] - df['close'].shift()), abs(df['low'] - df['close'].shift())], axis=1).max(axis=1)
        atr = tr.rolling(window).mean()
        atr_prev = tr.rolling(window).mean().shift(window)
        atr_ratio = atr / (atr_prev + 1e-10)  # 当前ATR相对过去ATR的比率，<1表示收缩
        # 布林带带宽
        ma = df['close'].rolling(window).mean()
        std = df['close'].rolling(window).std()
        bandwidth = (ma + 2*std - (ma - 2*std)) / (ma + 1e-10)
        # 价格相对带宽位置
        bb_pos = (df['close'] - (ma - 2*std)) / (4*std + 1e-10)
        # 收缩且靠近中轨 => 准备突破
        contraction = (atr_ratio < 0.7) & (bandwidth < bandwidth.rolling(2*window).quantile(0.1))
        price_mid = abs(bb_pos - 0.5) < 0.2
        raw = (contraction & price_mid).astype(float)
        # 方向用价格动量判断：若最近5日涨幅为正则为向上突破倾向，反之下跌
        momentum = (df['close'] - df['close'].shift(5)) / (df['close'].shift(5) + 1e-10)
        direction = np.sign(momentum)
        result = raw * direction
        # 平滑
        result = result.rolling(3).mean().fillna(0).clip(-1, 1)
        return result
