"""AI因子: 流动性磁铁反转风险 | 置信:60% | 检测价格在短时间内出现极端拉升或打压后迅速反转的形态，常见于流动性磁铁现象，导致追涨杀跌的亏损。通过计算价格极值后的反向动量强度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityMagnetReversalRisk(BaseFactor):
    """检测价格在短时间内出现极端拉升或打压后迅速反转的形态，常见于流动性磁铁现象，导致追涨杀跌的亏损。通过计算价格极值后的反向动量强度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liqmagnet",
            name="Liquidity Magnet Reversal Risk",
            display_name="流动性磁铁反转风险",
            description="检测价格在短时间内出现极端拉升或打压后迅速反转的形态，常见于流动性磁铁现象，导致追涨杀跌的亏损。通过计算价格极值后的反向动量强度。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算过去5根K线内的最高和最低
        roll_high = high.rolling(5).max()
        roll_low = low.rolling(5).min()
        # 判断当前价格是否接近近期高点或低点
        high_proximity = (close - roll_low) / (roll_high - roll_low + 1e-10)
        # 计算后续的反转强度：过去3根K线价格变化方向相反
        ret_1 = close.pct_change(1)
        ret_3 = close.pct_change(3)
        # 反转信号：近期大幅上涨后回调（高位反转）或大跌后反弹
        reversal_signal = np.where(
            (high_proximity > 0.9) & (ret_1 < 0) & (ret_3 > 0.02),
            1,
            np.where(
                (high_proximity < 0.1) & (ret_1 > 0) & (ret_3 < -0.02),
                -1,
                0
            )
        )
        # 用ATA平滑并归一化
        result = pd.Series(reversal_signal, index=close.index).ewm(span=3).mean()
        return result.fillna(0).clip(-1,1)
