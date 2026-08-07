"""AI因子: 噪音信号比 | 置信:55% | 计算日内价格微小反向波动的占比，衡量市场噪音程度。高噪音往往意味着趋势不明、容易引发止损和错误交易，对应亏损模式中的regime=unknown。通过比较相邻收盘价变化的方向与日内振幅的比例来识别噪音。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class NoiseToSignalRatio(BaseFactor):
    """计算日内价格微小反向波动的占比，衡量市场噪音程度。高噪音往往意味着趋势不明、容易引发止损和错误交易，对应亏损模式中的regime=unknown。通过比较相邻收盘价变化的方向与日内振幅的比例来识别噪音。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_noise_signal",
            name="Noise to Signal Ratio",
            display_name="噪音信号比",
            description="计算日内价格微小反向波动的占比，衡量市场噪音程度。高噪音往往意味着趋势不明、容易引发止损和错误交易，对应亏损模式中的regime=unknown。通过比较相邻收盘价变化的方向与日内振幅的比例来识别噪音。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: pd.DataFrame with columns ['open','high','low','close','volume']
        import numpy as np
        # 计算日内振幅
        daily_range = data['high'] - data['low']
        # 计算收盘价变化（绝对值）
        close_change = data['close'].diff().abs()
        # 避免除以零：用振幅+微小量
        ratio = close_change / (daily_range + 1e-10)
        # 将ratio限制在0-1之间，越小说明噪音越大（收盘变动远小于日内振幅）
        # 但我们要输出高噪音为-1，低噪音为+1，所以取负
        # 使用滚动窗口平均，例如20期
        roll_ratio = ratio.rolling(window=20).mean().fillna(0.5)
        # 映射到[-1,1]，0.5对应0，低于0.2为-1，高于0.8为+1
        result = -1 + 2 * (roll_ratio.clip(0.2, 0.8) - 0.2) / 0.6
        result = result.clip(-1, 1)
        return result
