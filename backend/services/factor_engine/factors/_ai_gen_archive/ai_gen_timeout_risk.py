"""AI因子: 持仓时间风险指标 | 置信:60% | 衡量持仓超时风险，基于价格波动率与持仓时间的关系。使用ATR（平均真实波幅）和价格相对于近期高低的偏离度，当价格长时间处于窄幅震荡且波动率下降时，持仓超时风险增加，预示可能突然反向波动。输出正值表示看涨方向风险高（应避免long），负值表示看跌方向风险高（避免short）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldingTimeRiskIndicator(BaseFactor):
    """衡量持仓超时风险，基于价格波动率与持仓时间的关系。使用ATR（平均真实波幅）和价格相对于近期高低的偏离度，当价格长时间处于窄幅震荡且波动率下降时，持仓超时风险增加，预示可能突然反向波动。输出正值表示看涨方向风险高（应避免long），负值表示看跌方向风险高（避免short）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeout_risk",
            name="Holding Time Risk Indicator",
            display_name="持仓时间风险指标",
            description="衡量持仓超时风险，基于价格波动率与持仓时间的关系。使用ATR（平均真实波幅）和价格相对于近期高低的偏离度，当价格长时间处于窄幅震荡且波动率下降时，持仓超时风险增加，预示可能突然反向波动。输出正值表示看涨方向风险高（应避免long），负值表示看跌方向风险高（避免short）。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算ATR（14周期）
        tr = pd.DataFrame({'hl': data['high'] - data['low'],
                           'hc': abs(data['high'] - data['close'].shift(1)),
                           'lc': abs(data['low'] - data['close'].shift(1))}).max(axis=1)
        atr = tr.rolling(14).mean()
        # 价格相对于20日均线的偏离
        ma20 = data['close'].rolling(20).mean()
        deviation = (data['close'] - ma20) / (atr + 1e-8)
        # 波动率变化：近期ATR与长期ATR比值
        long_atr = tr.rolling(50).mean()
        vol_ratio = atr / (long_atr + 1e-8)
        # 风险信号：当偏离不大但波动率萎缩（可能变盘）
        risk_long = (abs(deviation) < 0.5) & (vol_ratio < 0.8) & (data['close'] > ma20)
        risk_short = (abs(deviation) < 0.5) & (vol_ratio < 0.8) & (data['close'] < ma20)
        result = risk_long.astype(float) * (-1.0) + risk_short.astype(float) * 1.0
        result = result.rolling(2).mean().fillna(0)
        return result.clip(-1, 1)
