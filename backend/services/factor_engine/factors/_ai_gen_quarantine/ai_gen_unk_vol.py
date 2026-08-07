"""AI因子: 未知状态波动率比率 | 置信:60% | 通过比较短期波动率（过去5根K线）与长期波动率（过去20根K线）的比值，识别市场是否进入异常波动状态。当比值极低（<0.5）或极高（>2.0）时，表明波动率结构异常，可能处于未知状态，因子值接近-1；正常范围时接近+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class UnknownRegimeVolatilityRatio(BaseFactor):
    """通过比较短期波动率（过去5根K线）与长期波动率（过去20根K线）的比值，识别市场是否进入异常波动状态。当比值极低（<0.5）或极高（>2.0）时，表明波动率结构异常，可能处于未知状态，因子值接近-1；正常范围时接近+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unk_vol",
            name="UnknownRegimeVolatilityRatio",
            display_name="未知状态波动率比率",
            description="通过比较短期波动率（过去5根K线）与长期波动率（过去20根K线）的比值，识别市场是否进入异常波动状态。当比值极低（<0.5）或极高（>2.0）时，表明波动率结构异常，可能处于未知状态，因子值接近-1；正常范围时接近+1。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算典型价格
        tp = (data['high'] + data['low'] + data['close']) / 3
        # 短期波动率（5周期标准差）
        short_vol = tp.rolling(5).std()
        # 长期波动率（20周期标准差）
        long_vol = tp.rolling(20).std()
        # 波动率比率
        ratio = short_vol / long_vol
        # 处理无穷与NaN
        ratio = ratio.replace([np.inf, -np.inf], np.nan)
        # 映射到[-1,1]：当比值<0.5或>2时视为异常，越极端越接近-1
        # 使用log变换使映射更平滑
        log_ratio = np.log(ratio)
        # 正常范围大致 log(0.5)~log(2) ≈ -0.693~0.693，超出则线性映射
        # 定义异常阈值
        lower = -0.693  # log(0.5)
        upper = 0.693   # log(2)
        # 中间正常区映射到[0,1]，异常区映射到[-1,0]
        result = pd.Series(index=data.index, dtype=float)
        # 正常区域：线性映射从0到1
        normal_mask = (log_ratio >= lower) & (log_ratio <= upper)
        result[normal_mask] = (log_ratio[normal_mask] - lower) / (upper - lower) * 2 - 1  # 映射到[-1,1]但实际为[-1,1]? 先限制范围
        # 更简单：直接使用min-max到[-1,1]
        # 重新设计：用固定边界映射
        # 将log_ratio限制在[-3,3]内，然后线性映射到[-1,1]
        clipped = np.clip(log_ratio, -3, 3)
        result = -clipped / 3  # 当log_ratio为负（低波动）时result为正？不对，要相反
        # 纠正：我们希望异常波动（低或高）时接近-1，正常波动时接近+1。
        # 使用双曲正切变换：tanh( - (log_ratio - 0) / 1 ) ？
        # 简化：直接用异常评分 = 1 - abs(log_ratio)/0.693，再裁剪到[-1,1]
        # 更清晰的实现：
        ratio_filled = ratio.fillna(1.0)  # NaN时视为正常
        # 定义异常程度：离1越远越异常
        deviation = np.abs(np.log(ratio_filled + 1e-10))  # 避免log0
        # 归一化，假设最大偏离3
        score = 1 - deviation / 2.0
        score = np.clip(score, -1, 1)
        result = score
        return result
