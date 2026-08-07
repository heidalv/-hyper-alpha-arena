"""AI因子: 持仓超时反转 | 置信:50% | 模拟hold_timeout_review模式，检测价格在一定时间内偏离均线过远后发生反转。使用布林带偏离度与持仓时间（假设连续N周期未回归）结合，偏离越大且持续时间越长则反转概率越高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldTimeoutReversal(BaseFactor):
    """模拟hold_timeout_review模式，检测价格在一定时间内偏离均线过远后发生反转。使用布林带偏离度与持仓时间（假设连续N周期未回归）结合，偏离越大且持续时间越长则反转概率越高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_hold_to",
            name="Hold Timeout Reversal",
            display_name="持仓超时反转",
            description="模拟hold_timeout_review模式，检测价格在一定时间内偏离均线过远后发生反转。使用布林带偏离度与持仓时间（假设连续N周期未回归）结合，偏离越大且持续时间越长则反转概率越高。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        # 布林带 (20,2)
        ma20 = df['close'].rolling(20).mean()
        std20 = df['close'].rolling(20).std()
        upper = ma20 + 2 * std20
        lower = ma20 - 2 * std20
        # 价格偏离程度（%距离中轨）
        df['deviation'] = (df['close'] - ma20) / ma20
        # 连续偏离天数计数（超过±0.5%视为偏离）
        df['above'] = (df['close'] > upper).astype(int)
        df['below'] = (df['close'] < lower).astype(int)
        # 使用累计计数（不重置）
        df['above_count'] = df['above'].groupby((df['above'] != df['above'].shift()).cumsum()).cumcount() + 1
        df['below_count'] = df['below'].groupby((df['below'] != df['below'].shift()).cumsum()).cumcount() + 1
        # 当连续超出布林带至少2天且偏离度超过一定阈值时，认为有反转
        long_condition = (df['below'] == 1) & (df['below_count'] >= 2) & (df['deviation'] < -0.03)
        short_condition = (df['above'] == 1) & (df['above_count'] >= 2) & (df['deviation'] > 0.03)
        # 信号强度随偏离天数增长
        df['signal'] = 0.0
        df.loc[long_condition, 'signal'] = np.minimum(df['below_count'] * 0.3, 1.0)
        df.loc[short_condition, 'signal'] = -np.minimum(df['above_count'] * 0.3, 1.0)
        return df['signal'].fillna(0.0).clip(-1, 1)
