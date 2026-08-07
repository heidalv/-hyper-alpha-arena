"""AI因子: 成交量一致性 | 置信:50% | 检测成交量是否异常波动（如dust_cleanup的小额交易）。计算成交量相对于近期均值的偏离程度，偏离过大视为异常（负向），正常则视为正向。输出[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeConsistency(BaseFactor):
    """检测成交量是否异常波动（如dust_cleanup的小额交易）。计算成交量相对于近期均值的偏离程度，偏离过大视为异常（负向），正常则视为正向。输出[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volumeconsistency",
            name="Volume Consistency",
            display_name="成交量一致性",
            description="检测成交量是否异常波动（如dust_cleanup的小额交易）。计算成交量相对于近期均值的偏离程度，偏离过大视为异常（负向），正常则视为正向。输出[-1,1]。",
            category="volume",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        volume = data['volume']
        # 计算20日均量
        avg_vol = volume.rolling(20).mean()
        # 计算当前成交量与均值的比率
        ratio = volume / (avg_vol + 1e-10)
        # 使用对数变换，使分布更对称
        log_ratio = np.log(ratio + 1e-10)
        # 标准化：假设log_ratio均值为0，标准差0.5，映射到[-1,1]
        result = -np.clip(log_ratio / 0.5, -1, 1)
        # 解释：当ratio接近1时，log_ratio接近0，结果接近0；ratio远大于1（放量），log_ratio正，结果负；ratio远小于1（缩量），log_ratio负，结果正（但缩量不一定是坏信号，需谨慎）
        # 为了更匹配错误模式（dust_cleanup小额），我们让缩量也负向？实际上dust_cleanup是小额，所以成交量小，我们应让成交量小也视为负向。
        # 将ratio<0.5和ratio>2视为异常，中间为正常。
        result = 1 - 2 * np.clip(np.abs(ratio - 1) / 0.5, 0, 1)
        return result.fillna(0)
