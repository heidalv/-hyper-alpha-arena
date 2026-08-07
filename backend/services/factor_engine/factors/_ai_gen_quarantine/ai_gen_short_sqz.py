"""AI因子: 空头挤压压力 | 置信:60% | 通过价格连续上涨与加速下跌后的快速反弹特征，识别潜在空头挤压环境。计算逻辑：使用价格加速度（连续两日收益率差）和成交量变化率，构建多空力量对比。正值表示挤压风险高（做空危险），负值表示持续下跌环境。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortSqueezePressure(BaseFactor):
    """通过价格连续上涨与加速下跌后的快速反弹特征，识别潜在空头挤压环境。计算逻辑：使用价格加速度（连续两日收益率差）和成交量变化率，构建多空力量对比。正值表示挤压风险高（做空危险），负值表示持续下跌环境。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_short_sqz",
            name="Short Squeeze Pressure",
            display_name="空头挤压压力",
            description="通过价格连续上涨与加速下跌后的快速反弹特征，识别潜在空头挤压环境。计算逻辑：使用价格加速度（连续两日收益率差）和成交量变化率，构建多空力量对比。正值表示挤压风险高（做空危险），负值表示持续下跌环境。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        import pandas as pd
        import numpy as np
        # 参数
        n = 5
        # 收益率
        ret = data['close'].pct_change(1).replace([np.inf, -np.inf], 0)
        # 加速度：收益率的差分
        accel = ret.diff(1)
        # 成交量变化率
        vol_change = data['volume'].pct_change(1).replace([np.inf, -np.inf], 0)
        # 短期价格强度：过去n天涨幅除以波动
        up_sum = data['close'].diff(1).rolling(n).apply(lambda x: x[x>0].sum(), raw=True).fillna(0)
        down_sum = data['close'].diff(1).rolling(n).apply(lambda x: -x[x<0].sum(), raw=True).fillna(0)
        price_strength = (up_sum - down_sum) / (data['close'].rolling(n).std().replace(0, np.nan) * np.sqrt(n))
        # 合成信号：加速度为正且成交量放大，价格强度由负转正
        raw = accel * vol_change * price_strength
        # 滚动归一化
        std = raw.rolling(n).std().replace(0, np.nan)
        result = raw / std * 0.5
        result = result.clip(-1, 1)
        result = result.fillna(0)
        return result
