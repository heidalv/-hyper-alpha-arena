"""AI因子: 低波动反转因子 | 置信:60% | 通过价格波动率与成交量的比值，识别低波动环境下潜在的假突破和反转风险。当波动率低且成交量萎缩时，市场容易出现突然反转，导致止损或超时亏损。因子值接近+1表示高反转风险（做空信号），接近-1表示趋势延续（做多信号）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LowVolatilityReversal(BaseFactor):
    """通过价格波动率与成交量的比值，识别低波动环境下潜在的假突破和反转风险。当波动率低且成交量萎缩时，市场容易出现突然反转，导致止损或超时亏损。因子值接近+1表示高反转风险（做空信号），接近-1表示趋势延续（做多信号）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_lvr",
            name="Low_Volatility_Reversal",
            display_name="低波动反转因子",
            description="通过价格波动率与成交量的比值，识别低波动环境下潜在的假突破和反转风险。当波动率低且成交量萎缩时，市场容易出现突然反转，导致止损或超时亏损。因子值接近+1表示高反转风险（做空信号），接近-1表示趋势延续（做多信号）。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        # 计算真实波幅 ATR
        high = data['high']
        low = data['low']
        close = data['close']
        tr = pd.concat([high - low,
                        (high - close.shift()).abs(),
                        (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 标准化成交量
        vol_ma = data['volume'].rolling(14).mean()
        vol_ratio = data['volume'] / (vol_ma + 1e-10)
        # 低波动低成交信号：ATR/close 越小，vol_ratio越小
        norm_atr = atr / close
        # 反转信号：当ATR极低且成交量萎缩时，倾向于反转
        lvr = - (norm_atr.rank(pct=True) * vol_ratio.rank(pct=True))  # 负数：低波动低成交 => 高LVR值
        # 映射到[-1,1]
        result = lvr.map(lambda x: 2 * (x - 0.5) if not pd.isna(x) else 0)
        return result.clip(-1, 1)
