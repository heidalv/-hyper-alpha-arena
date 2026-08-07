"""AI因子: 短期反转强度 | 置信:60% | 捕捉开盘价与收盘价相对位置，结合成交量放大，识别短期多空失衡导致的止损模式，高正值表示多头反转风险，低负值表示空头反转风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortTermReversalIntensity(BaseFactor):
    """捕捉开盘价与收盘价相对位置，结合成交量放大，识别短期多空失衡导致的止损模式，高正值表示多头反转风险，低负值表示空头反转风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_short_term_reversal",
            name="Short-Term Reversal Intensity",
            display_name="短期反转强度",
            description="捕捉开盘价与收盘价相对位置，结合成交量放大，识别短期多空失衡导致的止损模式，高正值表示多头反转风险，低负值表示空头反转风险。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 日内价格位置：(close - open) / (high - low + 1e-10)
        intra_pos = (data['close'] - data['open']) / (data['high'] - data['low'] + 1e-10)
        # 成交量相对近期均值（20日）
        vol_ratio = data['volume'] / data['volume'].rolling(20).mean()
        # 反转信号：当日内位置极端且成交量异常
        # 多头反转：close接近高位但成交量小（衰竭），或close低位但成交量大（恐慌）
        signal = np.where(
            (intra_pos > 0.8) & (vol_ratio < 0.7), -1.0,
            np.where(
                (intra_pos < -0.8) & (vol_ratio > 1.5), 1.0,
                0.0
            )
        )
        # 平滑处理
        result = pd.Series(signal, index=data.index).rolling(3).mean().fillna(0.0)
        return result
