"""AI因子: 波动率爆发因子 | 置信:65% | 比较短期波动率与长期波动率的比率，当短期波动率突然放大时，市场容易产生假突破或止损触发，尤其做多后快速反向波动。使用H-L与收盘价变化的相对幅度。值域[-1,1]，正表示波动率异常高，负表示正常。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilitySurgeFactor(BaseFactor):
    """比较短期波动率与长期波动率的比率，当短期波动率突然放大时，市场容易产生假突破或止损触发，尤其做多后快速反向波动。使用H-L与收盘价变化的相对幅度。值域[-1,1]，正表示波动率异常高，负表示正常。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vos",
            name="VolatilitySurgeFactor",
            display_name="波动率爆发因子",
            description="比较短期波动率与长期波动率的比率，当短期波动率突然放大时，市场容易产生假突破或止损触发，尤其做多后快速反向波动。使用H-L与收盘价变化的相对幅度。值域[-1,1]，正表示波动率异常高，负表示正常。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        # 计算真实波幅TR
        tr = pd.DataFrame({
            'hl': high - low,
            'hc': (high - close.shift()).abs(),
            'lc': (low - close.shift()).abs()
        }).max(axis=1)
        # 短期5日平均TR，长期20日平均TR
        tr_short = tr.rolling(5).mean()
        tr_long = tr.rolling(20).mean()
        # 比率，规避分母为0
        ratio = tr_short / (tr_long + 1e-10)
        # 减去1得到偏离程度，再用tanh压缩到[-1,1]，正数表示短期波动过大
        result = np.tanh((ratio - 1.2) * 3)  # 1.2作为基准阈值
        return result.fillna(0)
