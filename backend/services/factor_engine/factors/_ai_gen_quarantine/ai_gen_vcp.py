"""AI因子: 波动率压缩比 | 置信:70% | 计算短期波动率与长期波动率的比值，当比值极低（市场处于低波动压缩状态）时，容易产生微小波动止损或超时亏损，因子值映射到[-1,1]，正值表示高风险（应避免交易），负值表示低风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility Compression Ratio(BaseFactor):
    """计算短期波动率与长期波动率的比值，当比值极低（市场处于低波动压缩状态）时，容易产生微小波动止损或超时亏损，因子值映射到[-1,1]，正值表示高风险（应避免交易），负值表示低风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vcp",
            name="Volatility Compression Ratio",
            display_name="波动率压缩比",
            description="计算短期波动率与长期波动率的比值，当比值极低（市场处于低波动压缩状态）时，容易产生微小波动止损或超时亏损，因子值映射到[-1,1]，正值表示高风险（应避免交易），负值表示低风险。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            import numpy as np
            # 短期波动率：过去5根K线的收盘价标准差
            short_vol = data['close'].pct_change().rolling(5).std()
            # 长期波动率：过去20根K线的收盘价标准差
            long_vol = data['close'].pct_change().rolling(20).std()
            ratio = short_vol / (long_vol + 1e-10)
            # 将ratio映射到[-1,1]，使用tanh归一化，中心在1附近（正常比值约1左右）
            # 当ratio < 0.5时认为压缩明显，ratio>1.5时认为扩张
            normalized = 1 - 2 * np.clip((ratio - 0.5) / 1.0, 0, 1)  # 0.5->1, 1.5->-1
            return normalized.fillna(0).clip(-1,1)
