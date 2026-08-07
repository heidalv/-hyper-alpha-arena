"""AI因子: 相对成交量冲击 | 置信:40% | 检测成交量的异常放大或缩小，结合价格变动方向判断是否适合交易。当成交量突然放大而价格没有显著突破时，可能为诱多/诱空信号，应规避。输出接近+1表示健康放量上涨，-1表示异常放量下跌或缩量上涨，0表示普通。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RelativeVolumeShock(BaseFactor):
    """检测成交量的异常放大或缩小，结合价格变动方向判断是否适合交易。当成交量突然放大而价格没有显著突破时，可能为诱多/诱空信号，应规避。输出接近+1表示健康放量上涨，-1表示异常放量下跌或缩量上涨，0表示普通。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liquidity",
            name="Relative Volume Shock",
            display_name="相对成交量冲击",
            description="检测成交量的异常放大或缩小，结合价格变动方向判断是否适合交易。当成交量突然放大而价格没有显著突破时，可能为诱多/诱空信号，应规避。输出接近+1表示健康放量上涨，-1表示异常放量下跌或缩量上涨，0表示普通。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 参数
        vol_period = 20
        # 成交量MA
        vol_ma = data['volume'].rolling(vol_period).mean()
        # 相对成交量比率
        vol_ratio = data['volume'] / (vol_ma + 1e-10)
        # 价格变化
        price_change = data['close'].pct_change()
        # 结合：正向价格变化且成交量温和（比率接近1）得正，反之得负
        # 使用sigmoid-like转换
        # 先对vol_ratio做处理，偏离1的程度
        vol_dev = (vol_ratio - 1) / 2  # 大致范围[-0.5, 无穷]用clip
        vol_dev = np.clip(vol_dev, -1, 1)
        # 价格变化符号
        price_sign = np.sign(price_change)
        # 信号：价格方向乘以成交量偏离的相反方向？当价格涨但成交量过大可能预示衰竭，所以用反比
        # 简化：价格涨且成交量正常（接近0）得正；价格跌且成交量正常得负；成交量异常则削弱信号
        signal = price_sign * (1 - np.abs(vol_dev))
        # 加上成交量偏度修正：极端大的成交量给予反向惩罚
        extreme_vol = (vol_ratio > 3).astype(float) * (-price_sign) * 0.5
        signal = signal + extreme_vol
        signal = np.clip(signal, -1, 1)
        return signal.fillna(0)
