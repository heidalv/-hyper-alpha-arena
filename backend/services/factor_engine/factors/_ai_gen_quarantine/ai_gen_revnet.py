"""AI因子: 反向净头寸风险因子 | 置信:60% | 识别短期价格剧烈反转且成交量放大的时刻，此类行情易导致反向净头寸亏损。通过计算过去N根K线的最高价与最低价之间的波动幅度，结合成交量相对均值的变化，输出[-1,1]表示反向风险程度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReverseNettingRisk(BaseFactor):
    """识别短期价格剧烈反转且成交量放大的时刻，此类行情易导致反向净头寸亏损。通过计算过去N根K线的最高价与最低价之间的波动幅度，结合成交量相对均值的变化，输出[-1,1]表示反向风险程度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_revnet",
            name="ReverseNettingRisk",
            display_name="反向净头寸风险因子",
            description="识别短期价格剧烈反转且成交量放大的时刻，此类行情易导致反向净头寸亏损。通过计算过去N根K线的最高价与最低价之间的波动幅度，结合成交量相对均值的变化，输出[-1,1]表示反向风险程度。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 参数设置
        n = 5  # 短期窗口
        m = 20 # 长期窗口
        # 计算短期波动幅度
        high_low = data['high'] - data['low']
        short_range = high_low.rolling(n).mean()
        # 计算价格反转强度：当前收盘价相对过去n天均值的变化方向与幅度
        ma_n = data['close'].rolling(n).mean()
        price_dev = (data['close'] - ma_n) / ma_n
        # 成交量异常度量
        vol_ma = data['volume'].rolling(m).mean()
        vol_ratio = data['volume'] / vol_ma.replace(0, np.nan)
        # 反转风险：当价格偏离均值较大且成交量放大时，风险高
        raw = -price_dev * vol_ratio * short_range
        # 归一化到[-1,1]
        std = raw.rolling(m).std().replace(0, np.nan)
        result = raw / std
        result = result.clip(-3, 3) / 3
        return result.fillna(0)
