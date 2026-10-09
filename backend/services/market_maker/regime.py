# -*- coding: utf-8 -*-
"""[h804 2026-10-04 用户指令"建立体系"] 形态分类器(阶段①)。

七维市场状态 → R1~R5 形态标签。设计见
`研究结论/流交易策略体系总体设计_20261004.md` §2。
阈值是**初版**,由进化循环(§5)持续调优,不是定死的。
"""
from __future__ import annotations

# 初版阈值(每项都是可进化的参数)
TH_FLOW = 0.15        # |OFI| ≥ 此值 = 有流
TH_TREND = 5.0        # |300s 趋势| ≥ 此值 = 有方向(bp)
TH_SIGMA_HI = 3.0     # σ_norm ≥ 此值 = 高波(需按币校准)
TH_SIGMA_RISE = 1.0   # σ 环比抬升 ≥ 此值 = 挤压启动
TH_RANGE_24H = 15.0   # 24h 振幅 > 此% = 跳空币
TH_LIQ_MIN = 3.0      # 该币成交率 < 此(腿/时) = 枯竭


def classify_regime(
    *,
    ofi: float = 0.0,
    trend300_bp: float = 0.0,
    sigma_norm: float = 0.0,
    sigma_rise: float = 0.0,
    range_pct_24h: float = 0.0,
    trades_per_hour: float = 999.0,
) -> str:
    """返回 'R1'~'R5'。

    优先级:R4(危险)> R5(枯竭)> R1(趋势流)> R3(挤压)> R2(平静)。
    每个输入缺失时取中性值 ⇒ 缺数据不会误判成危险。
    """
    # R4 高波跳空:σ 极端 或 该币 24h 振幅过大(跳空币)
    if sigma_norm >= TH_SIGMA_HI or range_pct_24h > TH_RANGE_24H:
        return "R4"
    # R5 枯竭盘:没有对手方
    if trades_per_hour < TH_LIQ_MIN:
        return "R5"
    # R1 趋势流:流强、方向明确、流与趋势同向(不 fade 流)
    if abs(ofi) >= TH_FLOW and abs(trend300_bp) >= TH_TREND \
            and ofi * trend300_bp > 0:
        return "R1"
    # R3 挤压突破:σ 从低位抬升(蓄力后的爆发)
    if sigma_rise >= TH_SIGMA_RISE:
        return "R3"
    return "R2"


REGIME_STRATEGY = {
    "R1": "S1_flow_trend",
    "R2": "S3_mean_revert",
    "R3": "S4_squeeze_breakout",
    "R4": "S5_risk_off",
    "R5": "S5_risk_off",
}
