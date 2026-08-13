"""AI因子: 趋势波动错配因子 | 置信:70% | 计算短周期(5日)与长周期(20日)动量方向的一致性，并结合近期波动率(14日ATR)与价格相对位置。若短长动量方向相反且波动率处于高位(>80%分位数)，表明趋势不明、风险较大，因子输出负值；反之输出正值。旨在捕捉regime=unknown时的高风险状态。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendVolatilityMismatchBeta(BaseFactor):
    """计算短周期(5日)与长周期(20日)动量方向的一致性，并结合近期波动率(14日ATR)与价格相对位置。若短长动量方向相反且波动率处于高位(>80%分位数)，表明趋势不明、风险较大，因子输出负值；反之输出正值。旨在捕捉regime=unknown时的高风险状态。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tvmb",
            name="TrendVolatilityMismatchBeta",
            display_name="趋势波动错配因子",
            description="计算短周期(5日)与长周期(20日)动量方向的一致性，并结合近期波动率(14日ATR)与价格相对位置。若短长动量方向相反且波动率处于高位(>80%分位数)，表明趋势不明、风险较大，因子输出负值；反之输出正值。旨在捕捉regime=unknown时的高风险状态。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 动量方向
        short_mom = close / close.shift(5) - 1
        long_mom = close / close.shift(20) - 1
        # 波动率
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr = tr.rolling(14).mean()
        atr_pct = atr / close * 100
        atr_rank = atr_pct.rolling(60).rank(pct=True)
        # 价格位置
        rolling_high = close.rolling(20).max()
        rolling_low = close.rolling(20).min()
        price_pos = (close - rolling_low) / (rolling_high - rolling_low + 1e-10)
        # 方向一致性
        direction_same = (short_mom > 0) == (long_mom > 0)
        # 规则：方向不一致且波动率>80%分位 => 负值；方向一致且波动率低 => 正值
        result = pd.Series(0.0, index=close.index)
        # 当方向一致且价格位置适中(0.3~0.7)给予正信号
        cond_bull = direction_same & (short_mom > 0) & (price_pos > 0.3) & (price_pos < 0.7) & (atr_rank < 0.6)
        cond_bear = direction_same & (short_mom < 0) & (price_pos > 0.3) & (price_pos < 0.7) & (atr_rank < 0.6)
        result[cond_bull] = 1.0
        result[cond_bear] = -1.0
        # 高风险区域：方向不一致且波动率>80%分位 => 强烈负信号
        cond_risk = (~direction_same) & (atr_rank > 0.8)
        result[cond_risk] = -1.0
        # 统一缩放到[-1,1]范围
        result = result.clip(-1, 1)
        return result
