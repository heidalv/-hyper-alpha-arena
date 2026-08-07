"""AI因子: 持仓时间风险因子 | 置信:60% | 模拟持仓超时风险：价格显著高于长期均线且波动率升高，表明市场不稳定，持仓时间长易遭遇回撤。因子值为负表示高风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Holding_Time_Risk_Factor(BaseFactor):
    """模拟持仓超时风险：价格显著高于长期均线且波动率升高，表明市场不稳定，持仓时间长易遭遇回撤。因子值为负表示高风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_holdtime_risk",
            name="Holding Time Risk Factor",
            display_name="持仓时间风险因子",
            description="模拟持仓超时风险：价格显著高于长期均线且波动率升高，表明市场不稳定，持仓时间长易遭遇回撤。因子值为负表示高风险。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 200周期移动平均
        ma200 = data['close'].rolling(200).mean()
        # 价格偏离百分比
        deviation = (data['close'] - ma200) / ma200
        # ATR (14周期)
        tr = pd.concat([data['high'] - data['low'],
                        (data['high'] - data['close'].shift(1)).abs(),
                        (data['low'] - data['close'].shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 波动率指标：ATR/价格
        volatility = atr / data['close']
        # 组合：高偏离+高波动 => 负信号
        raw = -deviation * volatility  # 偏离正数时乘波动率得负值，偏离负时得正值
        # 标准化
        result = np.tanh((raw - raw.rolling(60).mean()) / raw.rolling(60).std())
        result = result.fillna(0).clip(-1, 1)
        return result
