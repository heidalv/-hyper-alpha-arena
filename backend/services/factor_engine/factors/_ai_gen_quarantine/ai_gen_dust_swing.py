"""AI因子: 尘埃摆动 | 置信:40% | 检测连续小幅波动后的反向剧烈运动，模拟小单清理后大单反向。通过短期波动率的突然放大并与之前方向对比，给出反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class DustCleanupSwing(BaseFactor):
    """检测连续小幅波动后的反向剧烈运动，模拟小单清理后大单反向。通过短期波动率的突然放大并与之前方向对比，给出反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_dust_swing",
            name="Dust Cleanup Swing",
            display_name="尘埃摆动",
            description="检测连续小幅波动后的反向剧烈运动，模拟小单清理后大单反向。通过短期波动率的突然放大并与之前方向对比，给出反转信号。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算价格方向（1涨 -1跌）和波动率
        direction = np.sign(data['close'].diff())
        direction = direction.fillna(0)
        # 短期波动率：最近3根K线的绝对收益率标准差
        ret = data['close'].pct_change().fillna(0)
        short_vol = ret.rolling(3, min_periods=1).std()
        long_vol = ret.rolling(10, min_periods=1).std()
        # 波动率突变：短期/长期 - 1
        vol_spike = (short_vol / long_vol.replace(0, np.nan)) - 1
        # 判断前3根K线的平均方向
        prev_dir = direction.rolling(3, min_periods=1).mean().shift(1)
        # 信号：波动率大幅上升（>0.5）且当前方向与之前方向相反
        cond = (vol_spike > 0.5) & (direction * prev_dir < 0)
        signal = pd.Series(0, index=data.index)
        signal[cond] = -direction[cond]  # 反向信号，direction为1则信号-1，反之+1
        return signal.fillna(0)
