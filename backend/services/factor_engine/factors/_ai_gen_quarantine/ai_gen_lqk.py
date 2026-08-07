"""AI因子: 流动性枯竭因子 | 置信:60% | 检测成交量急剧萎缩伴随价格小幅波动的模式（dust_cleanup特征），通过计算成交量变化率与价格变化率的比值，当成交量骤降且价格变动极小时发出信号，避免在流动性不足时开仓。因子值归一化至[-1,1]，负值代表流动性枯竭风险（应避免做多），正值代表正常流动性。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityKillFactor(BaseFactor):
    """检测成交量急剧萎缩伴随价格小幅波动的模式（dust_cleanup特征），通过计算成交量变化率与价格变化率的比值，当成交量骤降且价格变动极小时发出信号，避免在流动性不足时开仓。因子值归一化至[-1,1]，负值代表流动性枯竭风险（应避免做多），正值代表正常流动性。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_lqk",
            name="Liquidity Kill Factor",
            display_name="流动性枯竭因子",
            description="检测成交量急剧萎缩伴随价格小幅波动的模式（dust_cleanup特征），通过计算成交量变化率与价格变化率的比值，当成交量骤降且价格变动极小时发出信号，避免在流动性不足时开仓。因子值归一化至[-1,1]，负值代表流动性枯竭风险（应避免做多），正值代表正常流动性。",
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
        # 成交量变化率
        vol_change = volume.pct_change()
        # 价格变化率（对数收益率）
        ret = np.log(close / close.shift(1))
        # 价格波动绝对值
        abs_ret = ret.abs()
        # 避免除零
        eps = 1e-10
        # 流动性枯竭指标：成交量下降而价格波动极低 => 负值
        # 使用vol_change和abs_ret的乘积，当均为负时乘积为正，取负号
        raw = -(vol_change * abs_ret).fillna(0)
        # 归一化到[-1,1]：使用tanh截断
        result = np.tanh(raw / (raw.std() + eps))
        # 当成交量变化为正且价格波动大时返回正值（正常），否则负值
        # 更精细：如果vol_change > 0且abs_ret > 0.5%则视为正常
        mask = (vol_change > 0) & (abs_ret > 0.005)
        result[mask] = 1.0
        # 极端负值：成交量萎缩超过50%且价格波动小于0.1%
        extreme = (vol_change < -0.5) & (abs_ret < 0.001)
        result[extreme] = -1.0
        return result.clip(-1,1)
