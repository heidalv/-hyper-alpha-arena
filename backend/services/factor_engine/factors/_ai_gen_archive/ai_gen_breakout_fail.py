"""AI因子: 假突破失败度 | 置信:65% | 捕捉价格突破近期高点后立即回落（假突破）的强度。亏损模式中多次出现max_hold_timeout，表明突破后动能不足。该因子衡量当前价格相对20日高点的位置，结合短期动量反转，值高表示处于假突破风险区。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Breakoutfailratio(BaseFactor):
    """捕捉价格突破近期高点后立即回落（假突破）的强度。亏损模式中多次出现max_hold_timeout，表明突破后动能不足。该因子衡量当前价格相对20日高点的位置，结合短期动量反转，值高表示处于假突破风险区。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_breakout_fail",
            name="BreakoutFailRatio",
            display_name="假突破失败度",
            description="捕捉价格突破近期高点后立即回落（假突破）的强度。亏损模式中多次出现max_hold_timeout，表明突破后动能不足。该因子衡量当前价格相对20日高点的位置，结合短期动量反转，值高表示处于假突破风险区。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high20 = data['high'].rolling(20).max()
        dist_high = (data['close'] - high20) / (high20 + 1e-9)
        ret3 = data['close'].pct_change(3)
        result = (dist_high * 5 - ret3).clip(-1, 1)
        return result
