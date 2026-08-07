"""AI因子: 加速度反转 | 置信:55% | 通过价格加速度（二阶差分）与RSI极值结合，捕捉趋势加速后的反转信号。当加速度由正转负且RSI大于70时，发出空头信号；反之当加速度由负转正且RSI小于30时，发出多头信号。输出值域[-1,1]，正值代表多头反转，负值代表空头反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Acceleration_Reversal(BaseFactor):
    """通过价格加速度（二阶差分）与RSI极值结合，捕捉趋势加速后的反转信号。当加速度由正转负且RSI大于70时，发出空头信号；反之当加速度由负转正且RSI小于30时，发出多头信号。输出值域[-1,1]，正值代表多头反转，负值代表空头反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ar",
            name="Acceleration Reversal",
            display_name="加速度反转",
            description="通过价格加速度（二阶差分）与RSI极值结合，捕捉趋势加速后的反转信号。当加速度由正转负且RSI大于70时，发出空头信号；反之当加速度由负转正且RSI小于30时，发出多头信号。输出值域[-1,1]，正值代表多头反转，负值代表空头反转。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        # 一阶差分（速度）和二阶差分（加速度）
        speed = close.diff()
        accel = speed.diff()
        # RSI 14
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss + 1e-8)
        rsi = 100 - (100 / (1 + rs))
        # 加速度方向变化: 前一个accel>0且当前<0 => 空头信号(-1); 前一个<0且当前>0 => 多头信号(+1)
        cross = np.sign(accel) * np.sign(accel.shift(1))
        # 仅在交叉时有效 (cross为-1表示方向变化)
        direction_change = (cross == -1).astype(float)
        # 结合RSI极值: 从正转负且RSI>70 -> 空头(-1), 从负转正且RSI<30 -> 多头(+1)
        bearish = direction_change * (accel.shift(1) > 0) * (rsi > 70)
        bullish = direction_change * (accel.shift(1) < 0) * (rsi < 30)
        raw = bullish.astype(float) * 1.0 - bearish.astype(float) * 1.0
        # 平滑处理，确保值域，无信号时返回0
        result = raw.rolling(3).mean().fillna(0)
        return result
