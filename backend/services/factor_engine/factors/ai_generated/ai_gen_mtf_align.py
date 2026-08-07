"""AI因子: 多时间框架一致性 | 置信:60% | 比较短期（5日）和长期（20日）移动平均线的斜率和偏差方向。若同向则给出+1，异向则-1，用于判断趋势同步性，避免在分歧时开仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Multi_Timeframe_Alignment(BaseFactor):
    """比较短期（5日）和长期（20日）移动平均线的斜率和偏差方向。若同向则给出+1，异向则-1，用于判断趋势同步性，避免在分歧时开仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mtf_align",
            name="Multi-Timeframe Alignment",
            display_name="多时间框架一致性",
            description="比较短期（5日）和长期（20日）移动平均线的斜率和偏差方向。若同向则给出+1，异向则-1，用于判断趋势同步性，避免在分歧时开仓。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        ma5 = close.rolling(5).mean()
        ma20 = close.rolling(20).mean()
        # 斜率（当前减去前一日）
        slope5 = ma5 - ma5.shift(1)
        slope20 = ma20 - ma20.shift(1)
        sign5 = np.sign(slope5)
        sign20 = np.sign(slope20)
        # 一致时方向乘以强度
        alignment = (sign5 * sign20).astype(float)
        # 用均线间距大小加强信号
        spread = (ma5 - ma20) / (ma20 + 1e-10)
        result = alignment * np.tanh(spread * 10)  # 缩放至[-1,1]
        result = result.fillna(0).clip(-1,1)
        return result
