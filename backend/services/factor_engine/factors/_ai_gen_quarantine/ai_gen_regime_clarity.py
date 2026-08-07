"""AI因子: 市场状态清晰度 | 置信:70% | 通过短期波动率（ATR/价格）与长期波动率的比值，结合ADX趋势强度，衡量市场是否处于清晰趋势或无序震荡。比值低且ADX高表示清晰趋势（信号强），比值高且ADX低表示混沌状态（信号弱）。返回[-1,1]，正值表示趋势清晰适合交易，负值表示混沌应回避。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeClarityIndicator(BaseFactor):
    """通过短期波动率（ATR/价格）与长期波动率的比值，结合ADX趋势强度，衡量市场是否处于清晰趋势或无序震荡。比值低且ADX高表示清晰趋势（信号强），比值高且ADX低表示混沌状态（信号弱）。返回[-1,1]，正值表示趋势清晰适合交易，负值表示混沌应回避。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_clarity",
            name="Regime Clarity Indicator",
            display_name="市场状态清晰度",
            description="通过短期波动率（ATR/价格）与长期波动率的比值，结合ADX趋势强度，衡量市场是否处于清晰趋势或无序震荡。比值低且ADX高表示清晰趋势（信号强），比值高且ADX低表示混沌状态（信号弱）。返回[-1,1]，正值表示趋势清晰适合交易，负值表示混沌应回避。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算ATR
        high = data['high']
        low = data['low']
        close = data['close']
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr_short = tr.rolling(7).mean() / close
        atr_long = tr.rolling(30).mean() / close
        ratio = atr_short / atr_long
        # ADX
        plus_dm = high.diff()
        minus_dm = low.diff()
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm > 0] = 0
        minus_dm = (-minus_dm)
        tr_smooth = tr.rolling(14).mean()
        plus_di = 100 * plus_dm.rolling(14).mean() / tr_smooth
        minus_di = 100 * minus_dm.rolling(14).mean() / tr_smooth
        dx = (plus_di - minus_di).abs() / (plus_di + minus_di) * 100
        adx = dx.rolling(14).mean()
        # 组合指标
        raw = (adx / 50) - (ratio - 1) * 2
        result = raw.clip(-1, 1)
        result = result.fillna(0)
        return result
