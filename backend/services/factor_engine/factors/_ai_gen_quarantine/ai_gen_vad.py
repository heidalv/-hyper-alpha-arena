"""AI因子: 成交量异常检测 | 置信:60% | 检测成交量相对于过去20日平均成交量的异常变化，同时结合价格方向。当成交量激增但价格未有效突破（或反转）时，常预示假突破风险。计算量比并乘上价格方向信号，归一化到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Anomaly_Detector(BaseFactor):
    """检测成交量相对于过去20日平均成交量的异常变化，同时结合价格方向。当成交量激增但价格未有效突破（或反转）时，常预示假突破风险。计算量比并乘上价格方向信号，归一化到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vad",
            name="Volume_Anomaly_Detector",
            display_name="成交量异常检测",
            description="检测成交量相对于过去20日平均成交量的异常变化，同时结合价格方向。当成交量激增但价格未有效突破（或反转）时，常预示假突破风险。计算量比并乘上价格方向信号，归一化到[-1,1]。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            import pandas as pd
            import numpy as np
            volume = data['volume']
            close = data['close']
            # 量比：当前量 / 过去20日均量
            vol_ma20 = volume.rolling(20).mean()
            vol_ratio = volume / vol_ma20.replace(0, np.nan)
            # 价格短期变化方向（5日百分比变化）
            ret5 = close.pct_change(5)
            # 用价格变化方向调整量比符号：如果量比大但价格变化小，视为异常
            # 构造异常分数：量比归一化（取log后z-score）乘以价格变化符号？
            # 这里简单使用：异常 = (vol_ratio - 1) * (1 - abs(ret5)*10)，但需归一化
            # 改用更稳健方法：计算量比的z-score，再与价格变化率正交
            log_vol_ratio = np.log(vol_ratio.replace(0, 0.0001))
            z_vol = (log_vol_ratio - log_vol_ratio.rolling(60).mean()) / log_vol_ratio.rolling(60).std()
            # 价格方向得分：使用价格变化率的符号和大小
            price_sign = np.sign(ret5).fillna(0)
            price_mag = ret5.abs().fillna(0)
            # 组合：当量异常且价格变化小（趋于0）时，信号负向表示风险
            anomaly = z_vol * (1 - price_mag * 2)  # 价格变化率0~0.5范围，1-2*0.5=0
            anomaly = anomaly.fillna(0)
            # 用tanh映射到[-1,1]
            result = np.tanh(anomaly)
            return result.fillna(0)
