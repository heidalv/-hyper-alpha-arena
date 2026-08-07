"""AI因子: 清算磁铁反转 | 置信:50% | 检测价格快速逼近近期极端清算区域（前20周期最高/最低）但未有效突破，同时伴随动量衰竭（RSI拐头或布林带外沿反转），预期价格向相反方向清算。当价格触及upper_band且RSI>70回落时看空；触及lower_band且RSI<30反弹时看多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Liquidationmagnetreversal(BaseFactor):
    """检测价格快速逼近近期极端清算区域（前20周期最高/最低）但未有效突破，同时伴随动量衰竭（RSI拐头或布林带外沿反转），预期价格向相反方向清算。当价格触及upper_band且RSI>70回落时看空；触及lower_band且RSI<30反弹时看多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liquidation_magnet",
            name="LiquidationMagnetReversal",
            display_name="清算磁铁反转",
            description="检测价格快速逼近近期极端清算区域（前20周期最高/最低）但未有效突破，同时伴随动量衰竭（RSI拐头或布林带外沿反转），预期价格向相反方向清算。当价格触及upper_band且RSI>70回落时看空；触及lower_band且RSI<30反弹时看多。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        period = 20
        # 布林带
        sma = data['close'].rolling(period).mean()
        std = data['close'].rolling(period).std()
        upper = sma + 2 * std
        lower = sma - 2 * std
        # RSI
        delta = data['close'].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        # 条件
        cond_short = (data['high'] >= upper) & (rsi.shift(1) > 70) & (data['close'] < upper)
        cond_long = (data['low'] <= lower) & (rsi.shift(1) < 30) & (data['close'] > lower)
        result = pd.Series(0.0, index=data.index)
        result[cond_short] = -1.0
        result[cond_long] = 1.0
        return result
