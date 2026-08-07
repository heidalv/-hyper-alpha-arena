"""AI因子: 尾盘动量衰减因子 | 置信:60% | 计算收盘前1小时价格变化与全天动量之间的差异。如果尾盘走势弱于全天平均动量，则预示次日反转可能。使用收盘价与开盘价作为全天动量，尾盘动量用最后N根K线（如12根5分钟K线）的收益率。输出[-1,1]正值表示尾盘动量衰减（做空/平多），负值表示尾盘加速。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TailMomentumDecay(BaseFactor):
    """计算收盘前1小时价格变化与全天动量之间的差异。如果尾盘走势弱于全天平均动量，则预示次日反转可能。使用收盘价与开盘价作为全天动量，尾盘动量用最后N根K线（如12根5分钟K线）的收益率。输出[-1,1]正值表示尾盘动量衰减（做空/平多），负值表示尾盘加速。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tailmom",
            name="Tail_Momentum_Decay",
            display_name="尾盘动量衰减因子",
            description="计算收盘前1小时价格变化与全天动量之间的差异。如果尾盘走势弱于全天平均动量，则预示次日反转可能。使用收盘价与开盘价作为全天动量，尾盘动量用最后N根K线（如12根5分钟K线）的收益率。输出[-1,1]正值表示尾盘动量衰减（做空/平多），负值表示尾盘加速。",
            category="behavioral",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 假设数据为5分钟频率，则尾盘取最后12根（1小时）
        # 先计算全天收益率
        daily_ret = (data['close'] - data['open']) / data['open']
        # 计算尾盘收益率：取最近N=12根K线的累计收益率
        N = 12
        tail_ret = data['close'].rolling(N, min_periods=N).apply(lambda x: (x.iloc[-1] - x.iloc[0]) / x.iloc[0], raw=False)
        # 动量衰减 = 全天收益率 - 尾盘收益率（如果全天强但尾盘弱 => 正值）
        decay = daily_ret - tail_ret
        # 滚动标准化
        roll_mean = decay.rolling(50, min_periods=50).mean()
        roll_std = decay.rolling(50, min_periods=50).std()
        result = (decay - roll_mean) / (roll_std + 1e-10)
        result = result.clip(-3, 3) / 3
        return result.fillna(0)
