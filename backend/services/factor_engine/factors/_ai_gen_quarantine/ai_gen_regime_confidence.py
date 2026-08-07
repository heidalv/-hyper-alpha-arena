"""AI因子: 市场状态置信度 | 置信:50% | 综合趋势强度、波动率一致性及流动性，评估当前市场状态是否清晰（趋势或震荡）。当趋势明确且波动率低时，+1表示高置信度做趋势；当趋势模糊且波动率混乱时，-1表示应避免交易。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeConfidence(BaseFactor):
    """综合趋势强度、波动率一致性及流动性，评估当前市场状态是否清晰（趋势或震荡）。当趋势明确且波动率低时，+1表示高置信度做趋势；当趋势模糊且波动率混乱时，-1表示应避免交易。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_confidence",
            name="Regime Confidence",
            display_name="市场状态置信度",
            description="综合趋势强度、波动率一致性及流动性，评估当前市场状态是否清晰（趋势或震荡）。当趋势明确且波动率低时，+1表示高置信度做趋势；当趋势模糊且波动率混乱时，-1表示应避免交易。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: DataFrame with columns ['open','high','low','close','volume']
        import pandas as pd
        import numpy as np
        close = data['close']
        # 趋势强度: 使用ADX类似指标简化 (D+ - D-)
        high = data['high']
        low = data['low']
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr14 = tr.rolling(14).mean()
        # 方向性运动
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        dm_plus = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        dm_minus = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        di_plus = pd.Series(dm_plus).rolling(14).sum() / atr14.replace(0, np.nan) * 100
        di_minus = pd.Series(dm_minus).rolling(14).sum() / atr14.replace(0, np.nan) * 100
        dx = np.abs(di_plus - di_minus) / (di_plus + di_minus).replace(0, np.nan) * 100
        adx = dx.rolling(14).mean()
        # 波动率一致性: 用过去10日收益率的标准差与20日标准差比值
        ret = close.pct_change()
        vol_10 = ret.rolling(10).std()
        vol_20 = ret.rolling(20).std()
        vol_consistency = 1 - np.abs(vol_10 / vol_20.replace(0, np.nan) - 1)
        # 流动性: 成交量变异系数
        cv_vol = volume.rolling(20).std() / volume.rolling(20).mean().replace(0, np.nan)
        liquidity = 1 - cv_vol.clip(0, 2) / 2
        # 综合得分
        score = (adx / 100 - 0.5) * 2 * 0.5 + (vol_consistency - 0.5) * 2 * 0.3 + (liquidity - 0.5) * 2 * 0.2
        result = score.clip(-1, 1)
        return result.fillna(0)
