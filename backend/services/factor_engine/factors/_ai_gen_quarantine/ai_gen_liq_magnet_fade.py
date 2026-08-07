"""AI因子: 流动性磁铁反转检测 | 置信:55% | 识别可能由大额止损单（流动性磁铁）引发的价格反转。当价格大幅上涨后迅速回落且成交量异常放大时，可能是多头止损被触发，预示反转；同理下跌后快速反弹。因子正值表示看多反转，负值表示看空反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityMagnetReversalDetector(BaseFactor):
    """识别可能由大额止损单（流动性磁铁）引发的价格反转。当价格大幅上涨后迅速回落且成交量异常放大时，可能是多头止损被触发，预示反转；同理下跌后快速反弹。因子正值表示看多反转，负值表示看空反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liq_magnet_fade",
            name="Liquidity Magnet Reversal Detector",
            display_name="流动性磁铁反转检测",
            description="识别可能由大额止损单（流动性磁铁）引发的价格反转。当价格大幅上涨后迅速回落且成交量异常放大时，可能是多头止损被触发，预示反转；同理下跌后快速反弹。因子正值表示看多反转，负值表示看空反转。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算收益率
        ret = data['close'].pct_change()
        # 计算前5分钟（5根K线）的价格变动幅度
        ret5 = data['close'].pct_change(5)
        # 计算成交量变动
        vol_ratio = data['volume'] / data['volume'].rolling(5).mean()
        # 识别快速上涨后回落: 前5根涨幅>3%且当前收盘低于前一根? 或简单用符号逻辑
        # 构建信号: 当ret5 > 0.03 且 ret < -0.005 且 vol_ratio > 1.5 -> 空头反转信号 (因子负值)
        # 当ret5 < -0.03 且 ret > 0.005 且 vol_ratio > 1.5 -> 多头反转信号 (因子正值)
        # 使用阈值生成 -1,0,1 然后用平滑
        up_reversal = (ret5 < -0.03) & (ret > 0.005) & (vol_ratio > 1.5)
        down_reversal = (ret5 > 0.03) & (ret < -0.005) & (vol_ratio > 1.5)
        signal = pd.Series(0, index=data.index)
        signal[up_reversal] = 1
        signal[down_reversal] = -1
        # 指数平滑保持连续性
        result = signal.ewm(span=3, adjust=False).mean()
        # 确保范围在[-1,1]
        result = result.clip(-1, 1)
        return result
