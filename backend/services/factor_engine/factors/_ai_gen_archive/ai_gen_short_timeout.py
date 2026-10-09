"""AI因子: 空头超时风险代理 | 置信:50% | 捕捉在未知市场状态下空头持仓超时导致的亏损模式。通过价格相对均线位置和波动率变化，识别弱势反弹后可能延续下跌但易超时的标的，值越高越倾向于做空风险高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortTimeoutRegimeProxy(BaseFactor):
    """捕捉在未知市场状态下空头持仓超时导致的亏损模式。通过价格相对均线位置和波动率变化，识别弱势反弹后可能延续下跌但易超时的标的，值越高越倾向于做空风险高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_short_timeout",
            name="Short_Timeout_Regime_Proxy",
            display_name="空头超时风险代理",
            description="捕捉在未知市场状态下空头持仓超时导致的亏损模式。通过价格相对均线位置和波动率变化，识别弱势反弹后可能延续下跌但易超时的标的，值越高越倾向于做空风险高。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ma20 = close.rolling(20).mean()
        ma50 = close.rolling(50).mean()
        ret5 = close.pct_change(5)
        vol20 = close.pct_change().rolling(20).std()
        vol5 = close.pct_change().rolling(5).std()
        trend = (ma20 - ma50) / (ma50 + 1e-9)
        squeeze = (vol5 - vol20) / (vol20 + 1e-9)
        result = (trend * 0.7 + squeeze * 0.3) * (1 - abs(ret5)).clip(-1, 1)
        return result.clip(-1, 1)
