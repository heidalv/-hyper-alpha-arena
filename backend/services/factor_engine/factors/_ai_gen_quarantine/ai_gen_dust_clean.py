"""AI因子: 尘埃清理反转 | 置信:30% | 检测极小成交量（dust）下的异常价格变动，这种情形常出现在流动性枯竭后的清理行为（如交易所做市商撤单或对冲清理），随后可能快速回归。使用成交量排名分位数和价格加速度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class DustCleanupReversal(BaseFactor):
    """检测极小成交量（dust）下的异常价格变动，这种情形常出现在流动性枯竭后的清理行为（如交易所做市商撤单或对冲清理），随后可能快速回归。使用成交量排名分位数和价格加速度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_dust_clean",
            name="Dust Cleanup Reversal",
            display_name="尘埃清理反转",
            description="检测极小成交量（dust）下的异常价格变动，这种情形常出现在流动性枯竭后的清理行为（如交易所做市商撤单或对冲清理），随后可能快速回归。使用成交量排名分位数和价格加速度。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        volume = data['volume']
        open_ = data['open']
    
        # 成交量处于历史极低分位（低于5%）
        vol_rank = volume.rolling(50).apply(lambda x: (x.rank(pct=True).iloc[-1]), raw=False)
        dust_condition = vol_rank < 0.05
    
        # 价格加速度：最近两期涨跌幅的变化
        pct = close.pct_change()
        accel = pct.diff()  # 加速度
    
        # 加速度异常大（正或负），且成交量极低，预示后续反转
        # 使用z-score
        accel_mean = accel.rolling(20).mean()
        accel_std = accel.rolling(20).std()
        accel_z = (accel - accel_mean) / (accel_std + 1e-8)
    
        extreme_accel = accel_z.abs() > 2.0
    
        # 反转方向：如果加速度为正（价格快速上升）则后续看跌；加速度为负则后续看涨
        signal = pd.Series(0.0, index=data.index)
        bearish = dust_condition & extreme_accel & (accel_z > 2.0)
        bullish = dust_condition & extreme_accel & (accel_z < -2.0)
        signal[bearish] = -1.0
        signal[bullish] = 1.0
    
        # 平滑处理，避免信号频繁闪烁
        signal = signal * 0.5  # 降低权重
        return signal
