"""AI因子: 波动率异常因子 | 置信:65% | 检测当前波动率相对于近期均值是否异常偏高，并结合价格处于近期极端位置的程度，当两者同时出现时预示潜在反转或磁吸风险。因子值接近-1表示高风险，接近+1表示低风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAnomalyFactor(BaseFactor):
    """检测当前波动率相对于近期均值是否异常偏高，并结合价格处于近期极端位置的程度，当两者同时出现时预示潜在反转或磁吸风险。因子值接近-1表示高风险，接近+1表示低风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vola_anomaly",
            name="Volatility Anomaly Factor",
            display_name="波动率异常因子",
            description="检测当前波动率相对于近期均值是否异常偏高，并结合价格处于近期极端位置的程度，当两者同时出现时预示潜在反转或磁吸风险。因子值接近-1表示高风险，接近+1表示低风险。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        open_ = data['open']
    
        # 单根K线波动率(高-低)/开盘价
        vola = (high - low) / (open_ + 1e-10)
        # 过去20期平均波动率
        avg_vola = vola.rolling(window=20, min_periods=5).mean()
        # 波动率异常倍数
        vola_ratio = vola / (avg_vola + 1e-10)
    
        # 价格位置百分位 (过去20日高低)
        roll_high = high.rolling(window=20, min_periods=5).max()
        roll_low = low.rolling(window=20, min_periods=5).min()
        position = (close - roll_low) / (roll_high - roll_low + 1e-10)  # 0~1
        # 极端位置：接近0或1
        extreme = 1 - 2 * np.abs(position - 0.5)  # 0~1, 越极端越接近0
    
        # 结合：波动率异常高且位置极端 -> 风险大 -> 因子值负
        raw = - (vola_ratio - 1) * (1 - extreme)  # vola_ratio>1时正，极端时(1-extreme)大，负值更大
        # 截断并映射到[-1,1]
        result = np.clip(raw / (raw.abs().rolling(50).mean() + 1e-10), -1, 1)
        result = result.fillna(0)
        return result
