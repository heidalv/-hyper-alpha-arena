"""AI因子: 均值回归失效概率 | 置信:60% | 基于价格偏离移动平均线的程度和历史反转成功率，估计当前均值回归策略是否可能失效。当偏离度很高但历史该偏离水平下反转概率很低时，因子值接近1，提示反转交易需谨慎；反之接近-1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MeanReversionFailureProbability(BaseFactor):
    """基于价格偏离移动平均线的程度和历史反转成功率，估计当前均值回归策略是否可能失效。当偏离度很高但历史该偏离水平下反转概率很低时，因子值接近1，提示反转交易需谨慎；反之接近-1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mr_failure",
            name="Mean Reversion Failure Probability",
            display_name="均值回归失效概率",
            description="基于价格偏离移动平均线的程度和历史反转成功率，估计当前均值回归策略是否可能失效。当偏离度很高但历史该偏离水平下反转概率很低时，因子值接近1，提示反转交易需谨慎；反之接近-1。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算价格对20日均线的偏离度
        ma20 = data['close'].rolling(20).mean()
        deviation = (data['close'] - ma20) / (ma20 + 1e-10)
        # 计算历史反转强度: 用未来1期收益率的符号
        future_ret = data['close'].shift(-1) / data['close'] - 1
        # 滚动计算不同偏离度下的反转成功率（例如过去60周期）
        window = 60
        # 定义偏离度分桶
        bins = np.linspace(-0.1, 0.1, 21)
        # 为每个时间点找到其偏离度所属的桶，并计算该桶的历史反转成功率
        # 简化：使用偏离度绝对值作为权重，乘以未来收益率的相反数，再结合滚动平均值
        failure = -np.sign(deviation) * future_ret  # 如果未来方向与偏离方向相反（反转），则为正
        # 平滑
        failure_smooth = failure.rolling(window).mean()
        # 使用偏离度绝对值缩放：大幅度偏离且失败概率高时因子高
        weight = np.abs(deviation)
        score = weight * (1 - failure_smooth)  # 高偏离且低反转成功率
        # 标准化
        roll_mean = score.rolling(100).mean()
        roll_std = score.rolling(100).std()
        z = (score - roll_mean) / (roll_std + 1e-10)
        result = np.clip(z, -3, 3) / 3.0
        return result.fillna(0)
