"""AI因子: 价格稳定性因子 | 置信:60% | 基于近期价格波动率（ATR/收盘价）和成交量相对均值的偏离度，衡量价格稳定性。当价格稳定（低波动、成交量正常）时值为正，表明适合持仓；当价格剧烈波动或成交量异常时值为负，提示风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PriceStabilityFactor(BaseFactor):
    """基于近期价格波动率（ATR/收盘价）和成交量相对均值的偏离度，衡量价格稳定性。当价格稳定（低波动、成交量正常）时值为正，表明适合持仓；当价格剧烈波动或成交量异常时值为负，提示风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_stbl",
            name="Price Stability Factor",
            display_name="价格稳定性因子",
            description="基于近期价格波动率（ATR/收盘价）和成交量相对均值的偏离度，衡量价格稳定性。当价格稳定（低波动、成交量正常）时值为正，表明适合持仓；当价格剧烈波动或成交量异常时值为负，提示风险。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 参数
        atr_period = 14
        vol_period = 20
        # 计算ATR
        high_low = data['high'] - data['low']
        high_close = np.abs(data['high'] - data['close'].shift())
        low_close = np.abs(data['low'] - data['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(window=atr_period, min_periods=1).mean()
        # 相对波动率 (ATR/close)
        rel_volatility = atr / data['close']
        # 成交量相对均值偏离
        vol_ma = data['volume'].rolling(window=vol_period, min_periods=1).mean()
        vol_ratio = data['volume'] / vol_ma
        # 综合得分：低波动 + 成交量正常为正
        # 使用倒数或负向映射到[-1,1]
        # 方法: 将rel_volatility标准化，然后取负，使得低波动得正
        vol_norm = (rel_volatility - rel_volatility.rolling(100, min_periods=1).mean()) / rel_volatility.rolling(100, min_periods=1).std()
        vol_score = -np.clip(vol_norm, -2, 2) / 2  # 波动越低越正
        # 成交量异常惩罚：偏离1倍标准差以上为负
        vol_ratio_norm = (vol_ratio - 1) / vol_ratio.rolling(100, min_periods=1).std()
        vol_ratio_score = -np.clip(np.abs(vol_ratio_norm) - 1, 0, 2) / 2  # 偏离越大越负
        # 合成得分
        score = 0.5 * vol_score + 0.5 * vol_ratio_score
        # 平滑并限制范围
        result = score.rolling(3, min_periods=1).mean().fillna(0)
        result = np.clip(result, -1, 1)
        return result
