"""AI因子: 动量衰减指标 | 置信:50% | 衡量价格动量是否出现衰减，用于识别持仓超时或趋势衰竭，避免在行情尾声继续持仓。计算短期价格变化率（如3期）与中期价格变化率（如10期）的比值，当比值下降时表明加速度放缓。输出值域[-1,1]，负值表示上涨动量衰减（潜在转空），正值表示下跌动量衰减（潜在转多）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumDecayIndicator(BaseFactor):
    """衡量价格动量是否出现衰减，用于识别持仓超时或趋势衰竭，避免在行情尾声继续持仓。计算短期价格变化率（如3期）与中期价格变化率（如10期）的比值，当比值下降时表明加速度放缓。输出值域[-1,1]，负值表示上涨动量衰减（潜在转空），正值表示下跌动量衰减（潜在转多）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_decay",
            name="Momentum Decay Indicator",
            display_name="动量衰减指标",
            description="衡量价格动量是否出现衰减，用于识别持仓超时或趋势衰竭，避免在行情尾声继续持仓。计算短期价格变化率（如3期）与中期价格变化率（如10期）的比值，当比值下降时表明加速度放缓。输出值域[-1,1]，负值表示上涨动量衰减（潜在转空），正值表示下跌动量衰减（潜在转多）。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np

        # 参数
        short_period = 3
        long_period = 10

        # 价格收益率
        ret_short = data['close'].pct_change(short_period)
        ret_long = data['close'].pct_change(long_period)

        # 避免除以0，用绝对值处理
        # 动量加速度: 短期平均变化率 / 长期平均变化率（若长期变化率为负，方向反转）
        # 使用符号函数保留方向
        sign_long = np.sign(ret_long)
        # 计算比率，如果长期收益为0，设比率=短期收益符号
        with np.errstate(divide='ignore', invalid='ignore'):
            ratio = np.where(ret_long != 0, ret_short / ret_long, sign_long)

        # 将ratio映射到[-1,1]：通常ratio>1表示加速，<1表示减速
        # 对于上涨趋势(正long)，ratio<1表示衰减，此时我们希望值为负（提示可能反转）
        # 对于下跌趋势(负long)，ratio<1 (因为负/负得正) 实际上表示衰减减弱，值为正
        # 我们需要一个统一衰减指标：当绝对值下降时触发
        # 更好的做法：计算短期动量和长期动量的差异，标准化
        # 使用短期动量减去长期动量再除以波动率
        # 简单版本：短期与长期收益的差值
        decay = ret_short - ret_long
        # 归一化到[-1,1]用tanh
        decay = decay.clip(-0.1, 0.1) * 10  # 放大到[-1,1]附近
        result = np.tanh(decay)

        # 对于上涨趋势中衰减为负值，下跌趋势衰减为正值，符合预期
        return result.fillna(0)
