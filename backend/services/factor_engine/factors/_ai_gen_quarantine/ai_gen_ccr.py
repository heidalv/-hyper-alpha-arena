"""AI因子: 反转捕获比率 | 置信:55% | 结合价格相对位置和成交量变化，识别短期过度延伸后的反转风险。当价格接近近期高点且成交量萎缩时发出负信号，反之亦然。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ContrarianCaptureRatio(BaseFactor):
    """结合价格相对位置和成交量变化，识别短期过度延伸后的反转风险。当价格接近近期高点且成交量萎缩时发出负信号，反之亦然。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ccr",
            name="ContrarianCaptureRatio",
            display_name="反转捕获比率",
            description="结合价格相对位置和成交量变化，识别短期过度延伸后的反转风险。当价格接近近期高点且成交量萎缩时发出负信号，反之亦然。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        window = 10
        # 价格相对位置: (close - low_roll) / (high_roll - low_roll + 1e-8)
        high_roll = high.rolling(window).max()
        low_roll = low.rolling(window).min()
        price_position = (close - low_roll) / (high_roll - low_roll + 1e-8)
        # 成交量相对变化: 当前成交量与过去平均的比值
        vol_ma = volume.rolling(window).mean()
        vol_ratio = volume / (vol_ma + 1e-8)
        # 组合：价格高位且成交量低 -> 负信号（反转下跌风险）
        # 价格低位且成交量高 -> 正信号（反转上涨可能）
        # 使用乘积并归一化
        raw = (price_position - 0.5) * (1 - vol_ratio)  # price_position>0.5且vol_ratio<1 => 负
        # 归一化到[-1,1]：经验范围[-0.5,0.5]
        result = np.clip(raw * 2, -1, 1)
        return result
