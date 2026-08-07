"""
永续合约特化因子（P1.7，方案 §2.2.8 / P1.7）。

以 DSL 表达式（JSON AST）定义，复用 P1.1 算子集，进 P1.2 清洗同流程筛选。
fields：funding / oi / basis / liquidation（已在 P1.1 ALLOWED_FIELDS）。

因子设计（对标报告 §2.2.8）：
    1. funding_skew      极端 funding 反向信号（仓位均值回归）
    2. oi_divergence_up  价升 OI 降 = 空头逼仓（涨不可持续，反向）
    3. oi_divergence_dn  价降 OI 降 = 仓位平仓
    4. basis_meanrev     basis premium/discount 均值回归
    5. liquidation_pressure 清算簇作为波动催化/方向信号
    6. funding_momentum  funding 趋势（持续正 funding = 多头拥挤）

每个因子是 DSL AST（dict），经 P1.1 audit + parser 可直接求值/缓存。
"""
from __future__ import annotations

# 永续特化因子表达式集（DSL AST）。
# 经 P1.1 audit 通过后，进 P1.2 清洗（CPCV + 增量相关 + DSR/PBO）。
PERP_FACTOR_EXPRS: dict[str, dict] = {
    # 1. funding skew：极端正 funding = 多头拥挤 → 反向信号。
    #    funding 相对其均值的 z-score 的负值（高 funding → 负因子值 → 看空）
    "funding_skew": {
        "op": "div",
        "args": [
            {"op": "sub", "args": [{"f": "funding"}, {"op": "mean", "args": [{"f": "funding"}, {"c": 48}]}]},
            {"op": "std", "args": [{"f": "funding"}, {"c": 48}]},
        ],
    },

    # 2. OI divergence（价升 OI 降 = 空头逼仓，反向看跌）：
    #    returns 与 OI delta 的负相关 —— 价升 OI 降时该值为大负数
    "oi_divergence_up": {
        "op": "corr",
        "args": [
            {"f": "returns"},
            {"op": "delta", "args": [{"f": "oi"}, {"c": 5}]},
            {"c": 20},
        ],
    },

    # 3. basis 均值回归：basis 偏离均值的负 z-score（premium 过高 → 回归 → 看跌）
    "basis_meanrev": {
        "op": "div",
        "args": [
            {"op": "sub", "args": [
                {"op": "mean", "args": [{"f": "basis"}, {"c": 96}]},
                {"f": "basis"},
            ]},
            {"op": "std", "args": [{"f": "basis"}, {"c": 96}]},
        ],
    },

    # 4. funding momentum：funding 的 EMA（持续正 funding = 多头拥挤趋势）
    "funding_momentum": {
        "op": "ema",
        "args": [{"f": "funding"}, {"c": 24}],
    },

    # 5. liquidation pressure：清算量的滚动排名（高清算 = 极端行情）
    "liquidation_rank": {
        "op": "ts_rank",
        "args": [{"f": "liquidation"}, {"c": 48}],
    },

    # 6. OI 变化动量：OI 的 EMA 变化（OI 持续上升 = 新仓涌入）
    "oi_momentum": {
        "op": "delta",
        "args": [
            {"op": "ema", "args": [{"f": "oi"}, {"c": 12}]},
            {"c": 6},
        ],
    },

    # 7. funding × OI 交互：高 funding + 高 OI = 逼空风险（复合信号）
    "squeeze_risk": {
        "op": "mul",
        "args": [
            {"op": "ts_rank", "args": [{"f": "funding"}, {"c": 48}]},
            {"op": "ts_rank", "args": [{"f": "oi"}, {"c": 48}]},
        ],
    },
}


def get_perp_factor_names() -> list[str]:
    return list(PERP_FACTOR_EXPRS.keys())


def get_perp_factor_expr(name: str) -> dict:
    """获取单个永续因子的 DSL AST。"""
    if name not in PERP_FACTOR_EXPRS:
        raise KeyError(f"未知永续因子 '{name}'，可选: {get_perp_factor_names()}")
    return PERP_FACTOR_EXPRS[name]
