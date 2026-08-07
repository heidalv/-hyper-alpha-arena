"""AI因子: 未知状态风险指示 | 置信:55% | 通过比较短期、中期和长期趋势方向的一致性，以及波动率的异常程度，判断当前市场是否处于难以预测的未知状态。当各周期趋势冲突严重且波动率处于极端分位数时，因子输出负值，提示高失败风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Unknown_Regime_Risk_Indicator(BaseFactor):
    """通过比较短期、中期和长期趋势方向的一致性，以及波动率的异常程度，判断当前市场是否处于难以预测的未知状态。当各周期趋势冲突严重且波动率处于极端分位数时，因子输出负值，提示高失败风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unknown_regime",
            name="Unknown Regime Risk Indicator",
            display_name="未知状态风险指示",
            description="通过比较短期、中期和长期趋势方向的一致性，以及波动率的异常程度，判断当前市场是否处于难以预测的未知状态。当各周期趋势冲突严重且波动率处于极端分位数时，因子输出负值，提示高失败风险。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        # 计算不同周期的移动均线
        sma_5 = data['close'].rolling(5).mean()
        sma_10 = data['close'].rolling(10).mean()
        sma_20 = data['close'].rolling(20).mean()
        # 趋势方向：1表示上升，-1下降，0持平（用当前价格与均线比较）
        trend_5 = np.sign(data['close'] - sma_5).fillna(0)
        trend_10 = np.sign(data['close'] - sma_10).fillna(0)
        trend_20 = np.sign(data['close'] - sma_20).fillna(0)
        # 趋势冲突度：三个方向的标准差，越大表示越不一致
        trend_consistency = -pd.DataFrame({'t5': trend_5, 't10': trend_10, 't20': trend_20}).std(axis=1)

        # 波动率异常：使用20日波动率分位数
        returns = data['close'].pct_change()
        vol_20 = returns.rolling(20).std()
        vol_rank = vol_20.rank(pct=True)
        vol_anomaly = -np.abs(vol_rank - 0.5) * 2  # 中间为0，两端接近-1

        # 成交量异常：当前成交量相对20日均量的偏离
        vol_ma = data['volume'].rolling(20).mean()
        vol_ratio = data['volume'] / vol_ma
        vol_anomaly2 = -np.clip(np.abs(vol_ratio - 1) * 2, 0, 1)  # 偏离越大越负

        # 综合信号，权重可根据回测调整
        result = trend_consistency * 0.4 + vol_anomaly * 0.3 + vol_anomaly2 * 0.3
        result = result.clip(-1, 1)
        return result
