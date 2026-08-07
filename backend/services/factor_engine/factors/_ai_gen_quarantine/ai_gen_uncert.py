"""AI因子: 市场不确定性指数 | 置信:65% | 综合日内价格波动率、成交量异常和价格模式（如长影线）来量化市场状态的不确定性。高值表示当前市场环境混乱，类似于错误模式中的regime=unknown。计算步骤：1. 计算日内波动范围(high-low)/close；2. 计算成交量相对于近期均值的偏离；3. 计算上下影线比例；4. 将三个指标标准化后加权平均，并映射到[-1,1]，正值表示不确定性高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Regime_Uncertainty_Index(BaseFactor):
    """综合日内价格波动率、成交量异常和价格模式（如长影线）来量化市场状态的不确定性。高值表示当前市场环境混乱，类似于错误模式中的regime=unknown。计算步骤：1. 计算日内波动范围(high-low)/close；2. 计算成交量相对于近期均值的偏离；3. 计算上下影线比例；4. 将三个指标标准化后加权平均，并映射到[-1,1]，正值表示不确定性高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_uncert",
            name="Regime_Uncertainty_Index",
            display_name="市场不确定性指数",
            description="综合日内价格波动率、成交量异常和价格模式（如长影线）来量化市场状态的不确定性。高值表示当前市场环境混乱，类似于错误模式中的regime=unknown。计算步骤：1. 计算日内波动范围(high-low)/close；2. 计算成交量相对于近期均值的偏离；3. 计算上下影线比例；4. 将三个指标标准化后加权平均，并映射到[-1,1]，正值表示不确定性高。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算日内波动率
        daily_range = (data['high'] - data['low']) / data['close']
        atr_ratio = daily_range / daily_range.rolling(20).mean()
        # 成交量异常
        vol_ratio = data['volume'] / data['volume'].rolling(20).mean()
        # 上下影线比例：长影线表示多空分歧
        upper_shadow = (data['high'] - np.maximum(data['open'], data['close'])) / (data['high'] - data['low'] + 1e-10)
        lower_shadow = (np.minimum(data['open'], data['close']) - data['low']) / (data['high'] - data['low'] + 1e-10)
        shadow_ratio = (upper_shadow + lower_shadow) / 2
        # 综合得分
        raw = (atr_ratio * 0.4 + vol_ratio * 0.3 + shadow_ratio * 0.3)
        # 映射到[-1,1]，使用指数衰减或分位数
        raw = raw.rolling(20).apply(lambda x: (x[-1] - np.mean(x)) / (np.std(x) + 1e-10), raw=False)
        result = np.clip(raw, -3, 3) / 3.0  # 三倍标准差截断
        return result.fillna(0)
