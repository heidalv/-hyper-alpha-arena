"""AI因子: 趋势衰减因子 | 置信:60% | 衡量当前趋势的持续性和衰减速度，高衰减信号提示可能触发hold_timeout_review亏损。通过计算价格序列的自相关和动量衰减率，识别趋势即将反转的时刻。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendDecayFactor(BaseFactor):
    """衡量当前趋势的持续性和衰减速度，高衰减信号提示可能触发hold_timeout_review亏损。通过计算价格序列的自相关和动量衰减率，识别趋势即将反转的时刻。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tdf",
            name="Trend Decay Factor",
            display_name="趋势衰减因子",
            description="衡量当前趋势的持续性和衰减速度，高衰减信号提示可能触发hold_timeout_review亏损。通过计算价格序列的自相关和动量衰减率，识别趋势即将反转的时刻。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
    
        # 计算不同周期动量
        mom1 = close.pct_change(1)
        mom5 = close.pct_change(5)
        mom10 = close.pct_change(10)
    
        # 动量一致性：如果短、中、长期动量方向相同（正或负），趋势强；否则分歧
        sign_sum = np.sign(mom1) + np.sign(mom5) + np.sign(mom10)
        consistency = sign_sum.abs() / 3.0  # 0~1
    
        # 计算动量衰减率：最近5日平均动量 vs 过去20日平均动量
        recent_mom = mom1.rolling(5).mean()
        long_mom = mom1.rolling(20).mean()
        # 衰减率 = (长周期动量 - 短周期动量) / 衰减幅度
        decay = (long_mom - recent_mom).abs() / (close.rolling(20).std() + 1e-8)
    
        # 当一致性高但衰减率也高时，趋势可能即将结束
        raw = consistency * decay
        # 归一化到[-1,1]
        result = raw / (raw.rolling(60).max() + 1e-8)
        result = result.clip(0, 1) * 2 - 1  # 映射到-1到1
        return result
