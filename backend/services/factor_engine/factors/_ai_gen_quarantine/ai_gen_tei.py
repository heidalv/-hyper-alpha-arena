"""AI因子: 趋势衰竭指数 | 置信:65% | 基于RSI与布林带位置，识别趋势末端超买超卖衰竭。当价格长时间处于极端区域且动能减弱时产生反向信号，用于避免max_hold_timeout亏损。正值表示超卖反弹机会（做多），负值表示超买回调风险（做空）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendExhaustionIndex(BaseFactor):
    """基于RSI与布林带位置，识别趋势末端超买超卖衰竭。当价格长时间处于极端区域且动能减弱时产生反向信号，用于避免max_hold_timeout亏损。正值表示超卖反弹机会（做多），负值表示超买回调风险（做空）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tei",
            name="Trend Exhaustion Index",
            display_name="趋势衰竭指数",
            description="基于RSI与布林带位置，识别趋势末端超买超卖衰竭。当价格长时间处于极端区域且动能减弱时产生反向信号，用于避免max_hold_timeout亏损。正值表示超卖反弹机会（做多），负值表示超买回调风险（做空）。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        # RSI 14
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, 1e-9)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        # Bollinger Bands 20,2
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper = sma20 + 2 * std20
        lower = sma20 - 2 * std20
        pct_b = (close - lower) / (upper - lower + 1e-9)
        # normalize to [-1,1]
        rsi_norm = (rsi - 50.0) / 50.0
        bb_norm = 2.0 * (pct_b - 0.5)
        # exhaustion signal: negative for overbought, positive for oversold
        signal = -rsi_norm * bb_norm
        # amplify when both are extreme
        extreme_weight = (rsi_norm.abs() * bb_norm.abs()) ** 0.5
        result = signal * extreme_weight
        result = result.clip(-1.0, 1.0)
        return result
