"""AI因子: 持仓超时风险 | 置信:60% | 衡量价格偏离移动均线且布林带宽收缩的风险，此时持仓易回撤触发master_running或max_hold_timeout。输出负值表示高风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class hold_risk_indicator(BaseFactor):
    """衡量价格偏离移动均线且布林带宽收缩的风险，此时持仓易回撤触发master_running或max_hold_timeout。输出负值表示高风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_hri",
            name="hold_risk_indicator",
            display_name="持仓超时风险",
            description="衡量价格偏离移动均线且布林带宽收缩的风险，此时持仓易回撤触发master_running或max_hold_timeout。输出负值表示高风险。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        # 20周期均线和标准差
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        # 布林带宽: (上轨-下轨)/中轨
        bandwidth = 4 * std20 / ma20  # 2倍标准差上下轨，带宽4倍std
        # 价格偏离度: (close - ma20) / ma20
        deviation = (close - ma20) / ma20
        # 风险信号：高偏离 + 窄带宽 => 回归风险大
        risk = -abs(deviation) * (1 - bandwidth.clip(0, 0.2)/0.2)  # 带宽越窄，风险越大
        # 归一化到[-1,1]，假设偏离度极限5%
        result = risk / 0.05
        return result.clip(-1, 1).fillna(0)
