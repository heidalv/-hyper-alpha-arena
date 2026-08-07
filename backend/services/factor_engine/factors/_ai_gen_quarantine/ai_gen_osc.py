"""AI因子: 震荡状态识别因子 | 置信:60% | 识别市场是否处于震荡状态。通过计算收盘价在近期（20日）最高最低之间的相对位置，并结合ATR与价格的比率。当价格处于中间区域且波动率较低时，输出负值（-1到0），表示趋势不明；当价格突破或波动率较高时，输出正值（0到1），表示趋势可能形成。该因子旨在过滤掉regime=unknown时的无效信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class OscillationRegimeIdentifier(BaseFactor):
    """识别市场是否处于震荡状态。通过计算收盘价在近期（20日）最高最低之间的相对位置，并结合ATR与价格的比率。当价格处于中间区域且波动率较低时，输出负值（-1到0），表示趋势不明；当价格突破或波动率较高时，输出正值（0到1），表示趋势可能形成。该因子旨在过滤掉regime=unknown时的无效信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_osc",
            name="Oscillation Regime Identifier",
            display_name="震荡状态识别因子",
            description="识别市场是否处于震荡状态。通过计算收盘价在近期（20日）最高最低之间的相对位置，并结合ATR与价格的比率。当价格处于中间区域且波动率较低时，输出负值（-1到0），表示趋势不明；当价格突破或波动率较高时，输出正值（0到1），表示趋势可能形成。该因子旨在过滤掉regime=unknown时的无效信号。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算20日最高最低
        high_20 = data['high'].rolling(20).max()
        low_20 = data['low'].rolling(20).min()
        # 相对位置：0~1之间
        pos = (data['close'] - low_20) / (high_20 - low_20 + 1e-12)
        # 中枢偏离度：距离0.5的绝对差值，再放大到0~1
        mid_dev = 2 * np.abs(pos - 0.5)  # 0~1, 越大越偏离中心
        # ATR/价格比率
        tr = pd.concat([data['high'] - data['low'],
                        np.abs(data['high'] - data['close'].shift(1)),
                        np.abs(data['low'] - data['close'].shift(1))], axis=1).max(axis=1)
        atr = tr.rolling(20).mean()
        vol_ratio = atr / data['close']  # 相对波动
        # 当偏离中心且波动率较大时，表示趋势；否则震荡
        # 组合：趋势信号 = mid_dev * vol_ratio，再归一化到[-1,1]
        raw = mid_dev * vol_ratio
        # 用滚动中位数和标准差归一化？简单clip到[-1,1]
        # 先减去均值再除以标准差，然后tanh
        roll_mean = raw.rolling(50).mean()
        roll_std = raw.rolling(50).std() + 1e-12
        z = (raw - roll_mean) / roll_std
        result = np.clip(z, -1, 1)
        return result.fillna(0.0)
