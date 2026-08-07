"""AI因子: 流动性挤压因子 | 置信:60% | 检测成交量与价格变动的不一致性。当价格在小幅波动时成交量异常放大，可能意味着流动性陷阱或主力操纵，容易导致订单无法顺利执行而触发止损。因子负值提示流动性挤压风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquiditySqueezeFactor(BaseFactor):
    """检测成交量与价格变动的不一致性。当价格在小幅波动时成交量异常放大，可能意味着流动性陷阱或主力操纵，容易导致订单无法顺利执行而触发止损。因子负值提示流动性挤压风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liquidity_squeeze",
            name="Liquidity Squeeze Factor",
            display_name="流动性挤压因子",
            description="检测成交量与价格变动的不一致性。当价格在小幅波动时成交量异常放大，可能意味着流动性陷阱或主力操纵，容易导致订单无法顺利执行而触发止损。因子负值提示流动性挤压风险。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        # 计算价格变动绝对值（百分比）
        price_change = close.pct_change().abs()
        # 计算成交量变化率
        vol_change = volume.pct_change().abs()
        # 构造挤压指标：成交量放大但价格变动小 -> 高挤压值
        squeeze = vol_change / (price_change + 1e-8)  # 防止除0
        # 标准化：对squeeze取log并滚动平均
        log_squeeze = np.log1p(squeeze)
        avg_squeeze = log_squeeze.rolling(20).mean()
        # 减去历史均值，得到异常
        anomaly = log_squeeze - avg_squeeze
        # 使用tanh压缩到[-1,1]，负值表示异常挤压
        result = -np.tanh(2 * anomaly)
        return result.fillna(0)
