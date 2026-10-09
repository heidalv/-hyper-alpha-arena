# -*- coding: utf-8 -*-
"""G1 实测最优清单 —— P2 开放调参前必须先有的机械守卫。

为什么这是 P2 的第一件事
========================

P1 审计抓到一处冲突：

    LLM 建议 spread_mult 0.5 -> 0.7
      理由（自洽）：费项 -0.259bp 已超过价差 +0.149bp，应放宽挂宽以多赚价差
    但 H146 实测：0.5 / 1.3 / 1.8 的净额为 +0.377 / -0.245 / -0.552 bp/笔，越宽越差

LLM 的理论不知道我们实测出的「越宽越差」（宽档只有大行情才成交，
选择性偏差把逆向选择放大）。若没有这层守卫，P2 一开放就会把挂宽改成 0.7，
而我们已经有实测证据说明那是错的。

=> 本模块把「哪些参数已被实测锁定、锁定值是多少、允许偏离多少」固化成数据，
由守卫机械比对。LLM 无权覆盖实测结论。

三档权限
========

  LOCKED  —— 有强实测证据的最优值，只允许极窄偏离
  BOUNDED —— 有实测最优，但允许有限偏离
  FREE    —— 无实测基准（本清单里实际会被守卫拒绝）

每条都必须带证据来源，无来源的不进清单。

用法：
    .venv\\Scripts\\python.exe scripts\\measured_optimum.py
"""
from __future__ import annotations

OPTIMUM: dict = {
    "spread_mult": {
        "value": 0.5,
        # [修正] **不对称允许区间**。原设计是对称 ±0.15 ⇒ 放行了 0.6，
        # 而 H146 的最佳点在 0.5、1.3 为负、1.8 更差 —— **已知单调**。
        # 往**窄**方向有整个 0.5 以下的未探索区间（可能更好）；
        # 往**宽**方向则已有实测证明变差，**没有任何支撑**。
        # ⇒ 下界松（0.2，留出探索）、上界紧（0.55，几乎贴住实测最优）。
        "allow_down": 0.3,      # 可低至 0.20（未探索区间，允许试）
        "allow_up": 0.05,       # 可高至 0.55（>此值无实测支撑）
        "level": "LOCKED",
        "evidence": (
            "H146 三档扫描：0.5 / 1.3 / 1.8 的净额为 +0.377 / -0.245 / -0.552 bp/笔；"
            "笔每分 13.3 / 11.3 / 9.2。单调：越宽越差。"
            "行情项 +0.107 / -0.777 / -1.032 bp 同向印证："
            "宽档只有大行情才成交，选择性偏差把逆向选择放大。"
            "⚠️ 0.5 以下（0.2/0.3）扫描结果不一致（0.3 净 +0.085 / 0.2 净 +0.728），"
            "方差大 ⇒ 允许探索但不能当既定结论。"
        ),
        "note": ("放宽方向（大于 0.55）必须拒绝：实测已证单调变差且无支撑。"
                 "收窄方向可试到 0.2（未探索区间）。"),
    },
    "spread_mult_reduce": {
        "value": 0.4,
        "allow_down": 0.2,
        "allow_up": 0.15,
        "level": "BOUNDED",
        "evidence": (
            "H163：0.95 时出库单挂在对手方之后 2.5% 价差，排在盘口外、等不到成交"
            "（单边行情里持仓累积到 10 分 45 秒）。收紧到 0.4 后持仓中位 15 秒、"
            "最大 90 秒、超龄 0 个。"
        ),
        "note": "上限不许超过 0.6，再宽就接近「挂到盘口外」的老问题。",
    },
    "take_profit_bp": {
        "value": 12.0,
        "allow_down": 2.0,
        "allow_up": 2.0,
        "level": "BOUNDED",
        "evidence": (
            "H164/H165 两轮真实盘口模拟：T=12 规则期望 +2.560 bp/笔（最优），"
            "严格口径触发率 32.0% 约等于乐观口径 31.3%（证明价格到过就能成交）。"
            "实测兑现：9 笔真实止盈腿平均 +7.4369 bp/笔（理论 +8.0）。"
            "必须大于 taker 费 4bp：T=3 时整体期望 -0.733 bp，比不做还差。"
        ),
        "note": "下限不许低于 6.0（逼近 taker 成本），上限不许超过 30.0。",
    },
    "min_width_bp": {
        "value": 0.3,
        "allow_down": 0.2,
        "allow_up": 0.2,
        "level": "FREE",
        "evidence": (
            "无实测基准。它在 spread_mult 大于 0 时被旁路，"
            "只有 spread_mult 等于 0 的绝对 bp 路径才生效（core.py 第 271 行）。"
            "当前配置下它是近死参数。"
        ),
        "note": (
            "当前 spread_mult=0.5 大于 0，此参数不参与报价。"
            "LLM 调它不会有任何效果（F189 那一类「改了没生效」）。"
            "守卫应拒绝并说明原因，而不是放行。"
        ),
    },
    "k_inv": {
        "value": 1.0,
        "allow_down": 0.2,
        "allow_up": 0.2,
        "level": "BOUNDED",
        "evidence": (
            "core.py 注释记录的最优扫描：w=5 / k_inv=1.0 / hold=300s / stop_loss=0 "
            "得到 +0.643bp、+140 USD（比旧默认 -0.23bp 翻正）。"
            "但该扫描是在另一套参数（w_base_bp 路径）下做的，"
            "当前 spread_mult 路径下未重测。"
        ),
        "note": "证据来自不同参数时代，降级为 BOUNDED，允许小幅探索。",
    },
}

# 安全参数：任何情况下都不许 LLM 碰（即使它给出理由）
FORBIDDEN = {
    "stop_loss_bp", "stop_loss_vol_min", "stop_loss_fast_mult", "stop_maker_grace_sec",
    "compound_ratio", "max_net_directional_ratio", "max_net_exposure_ratio",
    "max_gross_notional_ratio", "max_symbol_notional_ratio", "daily_loss_stop_pct",
    "timeout_exit_maker_only", "reduce_quote_disabled", "take_profit_maker_grace_sec",
}

# 方向性硬约束（比 allow_delta 更强，来自实测的单调性）
DIRECTIONAL = {
    "spread_mult": ("max", 0.65),
    "spread_mult_reduce": ("max", 0.6),
    "take_profit_bp": ("min", 6.0),
}

_DIR_REASON = {
    "spread_mult": (
        "属放宽方向。H146 实测已证单调变差（0.5 最优、1.3 为负、1.8 更差），拒绝。"),
    "spread_mult_reduce": (
        "大于 0.6，出库单接近挂在盘口外"
        "（H163：0.95 时排在对手方之后、等不到成交），拒绝。"),
    "take_profit_bp": (
        "小于 6.0，逼近 taker 费 4bp；实测 T=3 时整体期望为负，拒绝。"),
}


def check_suggestion(key: str, value) -> tuple:
    """守卫 G1：判断一个参数建议是否被允许，返回 (allowed, reason)。"""
    if key in FORBIDDEN:
        return False, (
            "安全参数禁止调整（" + str(key) + "）：上界一旦可放宽，整套风控失效。"
            "这类调整属于人为决策。")

    spec = OPTIMUM.get(key)
    if spec is None:
        return False, str(key) + " 不在实测最优清单内，无证据支撑，拒绝"

    try:
        v = float(value)
    except Exception:
        return False, str(key) + " 的建议值不是数值"

    d = DIRECTIONAL.get(key)
    if d is not None:
        kind, bound = d
        if (kind == "max" and v > bound) or (kind == "min" and v < bound):
            return False, str(key) + "=" + str(v) + " " + _DIR_REASON[key]

    if key == "min_width_bp":
        return False, (
            "min_width_bp 在当前配置下不参与报价"
            "（spread_mult 大于 0 时被 core.py 第 271 行旁路），调它无效果，拒绝。")

    opt = float(spec["value"])
    ad_down = spec.get("allow_down")
    ad_up = spec.get("allow_up")
    delta = v - opt
    allowed_span = ad_down if delta < 0 else ad_up

    if allowed_span is None or abs(delta) > allowed_span:
        direction = "偏窄" if delta < 0 else "偏宽"
        return False, (
            str(key) + "=" + str(v) + " 相对实测最优 " + str(opt) + " " + direction
            + " " + format(abs(delta), ".3f")
            + "（该方向允许 " + str(allowed_span) + "），拒绝。"
            "证据：" + spec["evidence"][:160])

    direction = "偏窄" if delta < 0 else "偏宽"
    return True, (str(key) + ": " + str(opt) + " -> " + str(v)
                  + "（" + direction + " " + format(abs(delta), ".3f")
                  + "，该方向允许 " + str(allowed_span)
                  + "）在实测最优的允许区间内。")


def main() -> int:
    print("=" * 96)
    print("G1 实测最优清单（P2 守卫的机械基准）")
    print("=" * 96)
    print("\n  " + "参数".ljust(22) + "最优".rjust(8) + "允许偏离".rjust(10)
          + "  级别")
    print("  " + "-" * 54)
    for k, s in OPTIMUM.items():
        print("  " + k.ljust(22) + str(s["value"]).rjust(8)
              + str(s["allow_delta"]).rjust(10) + "  " + s["level"])

    for k, s in OPTIMUM.items():
        print("\n  [" + k + "] 最优 " + str(s["value"]))
        print("    证据：" + s["evidence"])
        print("    说明：" + s["note"])

    print("\n  禁止调整的安全参数（" + str(len(FORBIDDEN)) + " 个）：")
    print("    " + ", ".join(sorted(FORBIDDEN)))

    print("\n  ── 自检：P1 抓到的那条冲突应被拒绝 ──")
    cases = [("spread_mult", 0.7), ("spread_mult", 0.55), ("stop_loss_bp", 20.0),
             ("min_width_bp", 1.4), ("take_profit_bp", 4.0)]
    n_ok = 0
    for k, v in cases:
        ok, why = check_suggestion(k, v)
        n_ok += 1 if ok else 0
        print("    " + k + "=" + str(v) + " -> " + ("放行" if ok else "**拒绝**"))
        print("      " + why)

    print("\n  自检：5 条里放行 " + str(n_ok) + " 条（期望 1 条：spread_mult=0.55）")
    return 0 if n_ok == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
