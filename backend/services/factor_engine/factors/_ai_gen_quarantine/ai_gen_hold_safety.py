"""AI因子: 持仓安全度 | 置信:60% | 基于波动率变化与持仓时间窗口的乘积，衡量当前持仓的风险。当市场波动率低且持仓时间过长时，容易因突发波动或时间损耗导致亏损。因子值高表示安全（波动率稳定、持仓时间短），值低表示危险（波动率骤增或时间过长）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Hold_Safety(BaseFactor):
    """基于波动率变化与持仓时间窗口的乘积，衡量当前持仓的风险。当市场波动率低且持仓时间过长时，容易因突发波动或时间损耗导致亏损。因子值高表示安全（波动率稳定、持仓时间短），值低表示危险（波动率骤增或时间过长）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_hold_safety",
            name="Hold Safety",
            display_name="持仓安全度",
            description="基于波动率变化与持仓时间窗口的乘积，衡量当前持仓的风险。当市场波动率低且持仓时间过长时，容易因突发波动或时间损耗导致亏损。因子值高表示安全（波动率稳定、持仓时间短），值低表示危险（波动率骤增或时间过长）。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        volume = data['volume']
        # 计算20日历史波动率（对数收益率标准差）
        log_ret = np.log(close / close.shift(1))
        hist_vol = log_ret.rolling(20).std() * np.sqrt(252)  # 年化波动率
        # 计算波动率突变：当前波动率相对过去60日均值的变化
        vol_mean = hist_vol.rolling(60).mean()
        vol_shock = (hist_vol - vol_mean) / vol_mean.replace(0, np.nan)  # 百分比变化
        vol_shock = vol_shock.fillna(0).clip(-2, 2)  # 限幅
        # 持仓时间因子：假设平均持仓时间与成交量相关，成交量低时持有时间长
        vol_ma = volume.rolling(20).mean()
        volume_ratio = volume / vol_ma.replace(0, np.nan)
        # 低成交量意味着流动性差，持仓风险高
        liquidity_risk = 1 - volume_ratio.clip(0, 2) / 2  # 0~1之间，1为低流动性风险？这里反向：值高表示流动性好风险低
        liquidity_risk = liquidity_risk.fillna(0.5)
        # 结合：安全度 = 1 - (波动率突变绝对值 + 流动性风险)/2
        safety = 1 - (np.abs(vol_shock) + liquidity_risk) / 2
        safety = safety * 2 - 1  # 映射到[-1,1]
        safety = safety.fillna(0).clip(-1, 1)
        return safety
