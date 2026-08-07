"""AI因子: 市场状态不确定性因子 | 置信:65% | 量化多个时间周期趋势方向的不一致性，以及波动率异常。因子越负，市场状态越混乱（regime=unknown），做空风险越高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeUncertainty(BaseFactor):
    """量化多个时间周期趋势方向的不一致性，以及波动率异常。因子越负，市场状态越混乱（regime=unknown），做空风险越高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_uncertainty",
            name="Regime_Uncertainty",
            display_name="市场状态不确定性因子",
            description="量化多个时间周期趋势方向的不一致性，以及波动率异常。因子越负，市场状态越混乱（regime=unknown），做空风险越高。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算短期、中期、长期均线方向（斜率）
        data['ma5'] = data['close'].rolling(5).mean()
        data['ma20'] = data['close'].rolling(20).mean()
        data['ma60'] = data['close'].rolling(60).mean()
        # 方向：1上升，-1下降，0持平（用差分符号）
        def direction(series):
            diff = series.diff()
            return np.sign(diff).fillna(0)
        data['dir5'] = direction(data['ma5'])
        data['dir20'] = direction(data['ma20'])
        data['dir60'] = direction(data['ma60'])
        # 一致性得分：三个方向相同得1，否则取反
        def consistency(row):
            if row['dir5'] == row['dir20'] == row['dir60']:
                return 1.0  # 一致
            elif (row['dir5'] != 0) and (row['dir20'] != 0) and (row['dir60'] != 0) and (row['dir5'] != row['dir20']):
                return -1.0  # 完全冲突
            else:
                return -0.5  # 部分冲突
        data['consistency'] = data.apply(consistency, axis=1)
        # 波动率异常：当前ATR相对历史均值
        atr = data['high'].rolling(14).max() - data['low'].rolling(14).min()
        atr_ma = atr.rolling(50).mean()
        data['vol_anomaly'] = np.clip((atr / atr_ma - 1), -1, 1)
        # 综合因子：一致性为负且波动率异常则加强
        data['factor'] = data['consistency'] * (1 - abs(data['vol_anomaly'])) * 0.5 - data['vol_anomaly'] * 0.5
        return data['factor'].fillna(0).clip(-1, 1)
