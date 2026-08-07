"""AI因子: 流动性风险指示 | 置信:50% | 检测微观流动性枯竭风险，使用日内价格跳跃度量。计算（high - low）/ close作为波动幅度，与成交量对比。若波动幅度大但成交量萎缩，可能为流动性陷阱，后续易发生剧烈反转。因子值正表示流动性风险高（做空），负表示流动性充足（做多）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityRiskIndicator(BaseFactor):
    """检测微观流动性枯竭风险，使用日内价格跳跃度量。计算（high - low）/ close作为波动幅度，与成交量对比。若波动幅度大但成交量萎缩，可能为流动性陷阱，后续易发生剧烈反转。因子值正表示流动性风险高（做空），负表示流动性充足（做多）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liquidity_risk",
            name="Liquidity Risk Indicator",
            display_name="流动性风险指示",
            description="检测微观流动性枯竭风险，使用日内价格跳跃度量。计算（high - low）/ close作为波动幅度，与成交量对比。若波动幅度大但成交量萎缩，可能为流动性陷阱，后续易发生剧烈反转。因子值正表示流动性风险高（做空），负表示流动性充足（做多）。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        import numpy as np
        # 日内波幅比
        spread = (data['high'] - data['low']) / (data['close'] + 1e-9)
        # 成交量相对变化
        vol_ma = data['volume'].rolling(20).mean()
        vol_ratio = data['volume'] / (vol_ma + 1e-9)
        # 波幅大而成交量小 => 风险高
        risk = spread / (vol_ratio + 1e-9)
        # 滚动标准化
        risk_z = (risk - risk.rolling(20).mean()) / (risk.rolling(20).std() + 1e-9)
        result = np.tanh(risk_z)
        return result.fillna(0).clip(-1, 1)
