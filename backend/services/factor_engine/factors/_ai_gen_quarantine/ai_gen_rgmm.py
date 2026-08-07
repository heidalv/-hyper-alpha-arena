"""AI因子: 市场状态动量 | 置信:65% | 结合短期、中期和长期动量方向一致性，当三者同向且幅度足够时认为趋势明确，值接近+1（多头）或-1（空头）；不一致时接近0。旨在识别regime=known的趋势环境。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeMomentum(BaseFactor):
    """结合短期、中期和长期动量方向一致性，当三者同向且幅度足够时认为趋势明确，值接近+1（多头）或-1（空头）；不一致时接近0。旨在识别regime=known的趋势环境。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rgmm",
            name="Regime Momentum",
            display_name="市场状态动量",
            description="结合短期、中期和长期动量方向一致性，当三者同向且幅度足够时认为趋势明确，值接近+1（多头）或-1（空头）；不一致时接近0。旨在识别regime=known的趋势环境。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算不同周期的收益率
        ret_short = data['close'].pct_change(5)   # 短期5周期
        ret_mid = data['close'].pct_change(20)    # 中期20周期
        ret_long = data['close'].pct_change(60)   # 长期60周期
        # 定义方向信号：正为1，负为-1，零为0
        def sign_with_threshold(series, thr=0.0):
            s = np.sign(series - thr)
            s[(series.abs() < 0.005)] = 0  # 小幅度视为无方向
            return s
        sign_short = sign_with_threshold(ret_short)
        sign_mid = sign_with_threshold(ret_mid)
        sign_long = sign_with_threshold(ret_long)
        # 一致性得分：三个方向之和，范围-3到3
        consistency = sign_short + sign_mid + sign_long
        # 映射到[-1,1]，并考虑幅度加权
        magnitude = (ret_short.abs() + ret_mid.abs() + ret_long.abs()) / 3.0
        # 归一化幅度到[0,1]，使用最大历史百分位
        mag_rank = magnitude.rolling(100, min_periods=20).apply(lambda x: pd.qcut(x, q=100, labels=False, duplicates='drop').iloc[-1] if len(x)>=20 else 50)
        mag_norm = mag_rank / 100.0  # 0~1
        # 最终因子，方向由consistency的符号决定，强度由mag_norm和一致性幅度共同决定
        factor = np.sign(consistency) * (np.abs(consistency)/3.0 * 0.7 + mag_norm * 0.3)
        # 填充前向值，缺失用0
        factor = factor.fillna(0)
        return factor.clip(-1, 1)
