"""AI因子: 低波动陷阱 | 置信:65% | 当价格波动率处于历史低位时，市场容易产生假突破和意外止损。该因子衡量近期波动率相对于过去一段时间的百分位，值越高表示波动率越低（越可能触发止损），值为-1表示高波动，+1表示极低波动。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LowVolatilityTrap(BaseFactor):
    """当价格波动率处于历史低位时，市场容易产生假突破和意外止损。该因子衡量近期波动率相对于过去一段时间的百分位，值越高表示波动率越低（越可能触发止损），值为-1表示高波动，+1表示极低波动。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_lowvol",
            name="Low Volatility Trap",
            display_name="低波动陷阱",
            description="当价格波动率处于历史低位时，市场容易产生假突破和意外止损。该因子衡量近期波动率相对于过去一段时间的百分位，值越高表示波动率越低（越可能触发止损），值为-1表示高波动，+1表示极低波动。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算每日真实波动范围
        tr = np.maximum(data['high'] - data['low'], np.maximum(abs(data['high'] - data['close'].shift(1)), abs(data['low'] - data['close'].shift(1))))
        # 滚动20日平均真实波动率
        atr = tr.rolling(20).mean()
        # 对ATR取自然对数，然后滚动60日计算百分位
        log_atr = np.log(atr.replace(0, np.nan))
        rank = log_atr.rolling(60).rank(pct=True)
        # 映射到[-1,1]，低波动对应高正值
        result = 1 - 2 * rank
        # 处理前60天缺失值，填充为0（中性）
        result = result.fillna(0)
        return result
