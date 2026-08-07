"""AI因子: 流动性冲击微小反转因子 | 置信:60% | 检测成交量突然放大但价格变化极小的情形，这往往代表大单隐蔽建仓或平仓，随后易发生大幅反向波动（类似亏损模式中的master_running_close_tiny）。通过成交量与价格变动比率的异常值来捕捉。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Liquidity_Impact_Micro(BaseFactor):
    """检测成交量突然放大但价格变化极小的情形，这往往代表大单隐蔽建仓或平仓，随后易发生大幅反向波动（类似亏损模式中的master_running_close_tiny）。通过成交量与价格变动比率的异常值来捕捉。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liquid_impact",
            name="Liquidity_Impact_Micro",
            display_name="流动性冲击微小反转因子",
            description="检测成交量突然放大但价格变化极小的情形，这往往代表大单隐蔽建仓或平仓，随后易发生大幅反向波动（类似亏损模式中的master_running_close_tiny）。通过成交量与价格变动比率的异常值来捕捉。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            import numpy as np
            close = data['close']
            volume = data['volume']
            # 价格变动绝对值
            price_change = np.abs(close - close.shift(1))
            # 避免除以0
            price_change = price_change.replace(0, np.nan)
            # 成交量价格比（每单位价格变动对应的成交量）
            vp_ratio = volume / price_change
            # 标准化：Z-score
            vp_z = (vp_ratio - vp_ratio.rolling(20).mean()) / vp_ratio.rolling(20).std()
            # 高成交量冲击阈值（大于2个标准差）
            high_impact = vp_z > 2.0
            # 同时价格变化非常小（价格变动小于0.1%的ATR）
            tr = np.maximum(data['high'] - data['low'], np.abs(data['high'] - close.shift(1)), np.abs(data['low'] - close.shift(1)))
            atr = tr.rolling(10).mean()
            tiny_move = price_change < (0.001 * atr)
            # 条件合并
            condition = high_impact & tiny_move
            # 方向：根据后续1根K线的价格走势决定信号方向（实际交易中需用未来数据，此处用shift(-1)模拟）
            # 注意：在实盘使用时应基于当前信号方向，此处输出为方向性预测
            # 由于是因子输出，我们基于当前时刻的收盘价位置（靠近近期高低点）赋予方向
            recent_high = data['high'].rolling(10).max()
            recent_low = data['low'].rolling(10).min()
            # 若当前价格靠近近期高点，则预示向下反转（做空风险）
            near_high = (close >= recent_high * 0.98)
            near_low = (close <= recent_low * 1.02)
            signal = pd.Series(0.0, index=close.index)
            signal[condition & near_high] = -0.7
            signal[condition & near_low] = 0.7
            return signal.clip(-1, 1)
