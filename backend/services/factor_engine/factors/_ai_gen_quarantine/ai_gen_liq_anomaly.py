"""AI因子: 流动性异常检测 | 置信:60% | 检测价格大幅变动时成交量是否反常。计算归一化的价格变化与成交量变化之间的背离程度。当价格快速上涨/下跌但成交量萎缩时，可能预示流动性陷阱或虚假突破，因子值接近1表示异常；当量价齐升/齐降时，因子值接近-1表示健康。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityAnomalyDetector(BaseFactor):
    """检测价格大幅变动时成交量是否反常。计算归一化的价格变化与成交量变化之间的背离程度。当价格快速上涨/下跌但成交量萎缩时，可能预示流动性陷阱或虚假突破，因子值接近1表示异常；当量价齐升/齐降时，因子值接近-1表示健康。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liq_anomaly",
            name="Liquidity Anomaly Detector",
            display_name="流动性异常检测",
            description="检测价格大幅变动时成交量是否反常。计算归一化的价格变化与成交量变化之间的背离程度。当价格快速上涨/下跌但成交量萎缩时，可能预示流动性陷阱或虚假突破，因子值接近1表示异常；当量价齐升/齐降时，因子值接近-1表示健康。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 价格收益率
        returns = data['close'].pct_change()
        # 成交量变化率
        vol_change = data['volume'].pct_change()
        # 计算价格变动绝对值与成交量变化率的相关系数(滚动10期)
        # 用协方差归一化
        window = 10
        ret_abs = returns.abs()
        # 滚动协方差
        cov = ret_abs.rolling(window).cov(vol_change)
        # 各自方差
        var_ret = ret_abs.rolling(window).var()
        var_vol = vol_change.rolling(window).var()
        # 避免除以0
        corr = cov / (np.sqrt(var_ret * var_vol) + 1e-10)
        # 当corr为负且绝对值大时，表示价格变动大而成交量变化小(背离)，因子为正
        anomaly = -corr
        # 标准化
        roll_mean = anomaly.rolling(100).mean()
        roll_std = anomaly.rolling(100).std()
        z = (anomaly - roll_mean) / (roll_std + 1e-10)
        result = np.clip(z, -3, 3) / 3.0
        return result.fillna(0)
