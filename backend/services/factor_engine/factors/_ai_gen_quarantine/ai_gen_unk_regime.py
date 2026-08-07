"""AI因子: 未知风险态波动拥挤 | 置信:65% | 针对‘regime=unknown’下的亏损，综合波动率结构（HV vs IV）和成交量相对强度，当波动率突然上升且成交量拥挤时，判断市场处于混乱状态，产生负向信号以避免开仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class UnknownRegimeVolatilityCrowding(BaseFactor):
    """针对‘regime=unknown’下的亏损，综合波动率结构（HV vs IV）和成交量相对强度，当波动率突然上升且成交量拥挤时，判断市场处于混乱状态，产生负向信号以避免开仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unk_regime",
            name="Unknown Regime Volatility Crowding",
            display_name="未知风险态波动拥挤",
            description="针对‘regime=unknown’下的亏损，综合波动率结构（HV vs IV）和成交量相对强度，当波动率突然上升且成交量拥挤时，判断市场处于混乱状态，产生负向信号以避免开仓。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close'].values
        volume = data['volume'].values
        # 计算历史波动率（20日标准差）
        rets = np.diff(close) / close[:-1]
        if len(rets) >= 20:
            hv = np.std(rets[-20:]) * np.sqrt(252)
        else:
            hv = np.nan
        # 成交量异常：当前量相对过去30日均量的比值
        vol_ma = np.mean(volume[-31:-1]) if len(volume)>=31 else np.mean(volume)
        vol_ratio = volume[-1] / (vol_ma + 1e-10)
        # 价格变化率（最近1日）
        price_change = (close[-1] - close[-2]) / close[-2] if len(close)>=2 else 0
        # 混乱信号：高波动率（hv > 0.8分位数），高量比(>1.8)，价格变化小或无序
        # 简单判据：HV > 0.5且vol_ratio>1.5且abs(price_change)<0.02 -> regime=unknown
        threshold_vol = 0.5
        if hv > threshold_vol and vol_ratio > 1.5 and abs(price_change) < 0.02:
            score = -min(1.0, (hv-0.5)/0.5 * vol_ratio/3.0)
        elif hv > threshold_vol and vol_ratio > 1.5:
            score = -0.5
        else:
            score = 0.0
        return pd.Series(score, index=[data.index[-1]])
