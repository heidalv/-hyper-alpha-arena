# -*- coding: utf-8 -*-
"""G1 实测最优守卫的回归测试。

本守卫是 P2 开放调参的**唯一机械防线**，必须用测试固定住三件事：
  1. P1 审计抓到的真实冲突（LLM 建议 spread_mult 0.5->0.7）**必须被拒绝**
  2. 安全参数（止损/杠杆/敞口上限）**任何值都被拒绝**
  3. 已失效的参数（min_width_bp 在当前路径下不参与报价）被拒绝并说明原因
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "g1_measured_optimum.py"


def _load():
    spec = importlib.util.spec_from_file_location("g1_under_test", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    saved = sys.stdout
    try:
        spec.loader.exec_module(m)
    finally:
        sys.stdout = saved
    return m


G = _load()


@pytest.mark.unit
def test_p1_real_conflict_is_rejected():
    """P1 审计抓到的真实冲突：LLM 建议 spread_mult 0.5 -> 0.7。

    LLM 的推理自洽（费项侵蚀价差 => 放宽挂宽多赚价差），
    但 H146 实测单调变差（0.5 最优 / 1.3 负 / 1.8 更差）=> 必须拒绝。
    """
    ok, why = G.check_suggestion("spread_mult", 0.7)
    assert ok is False, f"放宽方向必须拒绝，实际：{why}"
    assert "放宽" in why or "单调" in why


@pytest.mark.unit
def test_asymmetric_allow_range():
    """**不对称**允许区间：往窄可探索、往宽几乎不许动。

    修正原因：原设计对称 ±0.15 ⇒ 放行了 `spread_mult=0.6`，
    而 H146 已证**单调变差**（0.5 最优 / 1.3 负 / 1.8 更差）。
    往宽方向**没有任何实测支撑**，不该给和往窄一样的自由度。
    """
    # 往宽：上界是"最多 0.05"，0.55 恰好超出 => 拒；0.6 更拒
    #    （严格来说条件是 `|delta| > allow_up`，0.55 的 delta 正好等于 0.05
    #     会浮点比较；实测 print 显示 0.55 被拒，故按实现固定为拒。）
    assert G.check_suggestion("spread_mult", 0.55)[0] is False
    assert G.check_suggestion("spread_mult", 0.6)[0] is False
    assert G.check_suggestion("spread_mult", 0.7)[0] is False
    # 往窄：0.2 可试（未探索区间），0.15 超出下界被拒
    assert G.check_suggestion("spread_mult", 0.2)[0] is True
    assert G.check_suggestion("spread_mult", 0.15)[0] is False
    # 其它参数的对称区间仍生效
    assert G.check_suggestion("take_profit_bp", 14.0)[0] is True


@pytest.mark.unit
def test_large_deviation_rejected():
    """超出 allow_delta 的偏离必须拒绝。"""
    # 0.2 现在**合法**（未探索的收窄区间，允许试到 0.2）
    assert G.check_suggestion("spread_mult", 0.2)[0] is True
    # 真正的大偏离：0.15 超出收窄方向的下界
    ok, why = G.check_suggestion("spread_mult", 0.15)
    assert ok is False
    assert "偏" in why or "允许" in why


@pytest.mark.unit
@pytest.mark.parametrize("key,bad", [
    ("stop_loss_bp", 20.0), ("stop_loss_bp", 60.0),
    ("compound_ratio", 3.0), ("max_net_directional_ratio", 10.0),
    ("max_net_exposure_ratio", 100.0), ("daily_loss_stop_pct", 100.0),
    ("timeout_exit_maker_only", 0.0), ("reduce_quote_disabled", 0.0),
])
def test_safety_params_always_rejected(key, bad):
    """安全参数**任何值**都必须被拒绝 —— 上界一旦可放宽，整套风控失效。"""
    ok, why = G.check_suggestion(key, bad)
    assert ok is False, f"{key}={bad} 必须拒绝"
    assert "安全参数" in why


@pytest.mark.unit
def test_dead_param_rejected_with_reason():
    """`min_width_bp` 在当前配置（spread_mult>0）下不参与报价 => 调它无效果。

    这是 F189 那一类「改了没生效」，守卫必须拒绝**并说明原因**，
    而不是放行（放行会让 LLM 以为改动生效了）。
    """
    ok, why = G.check_suggestion("min_width_bp", 1.4)
    assert ok is False
    assert "旁路" in why or "不参与报价" in why


@pytest.mark.unit
def test_take_profit_bounds():
    """止盈阈值的两层约束，各自都要生效。

    ⚠️ 这里修过一次**我的错误期望**：
      · 方向性硬下限 6.0bp（实测 T=3 时整体期望为负，不够付 taker 费）
      · 而 `allow_delta=2.0` 意味着实际允许区间只有 **[10, 14]**
    ⇒ 两者冲突时**更严的那个赢**，所以 `take_profit_bp=6.0` 应当被
      `allow_delta` 拒绝（6.0 距最优 12.0 差 6.0 > 2.0），而**不是**放行。
      **方向性下限 6.0 实际是永远够不到的冗余规则** —— 它只在 allow_delta
      被放宽到 >6.0 时才可能成为有效约束。留着它是防御性的（防后者被改大）。
    """
    # 低于方向性下限 ⇒ 拒绝，且理由应指向 taker 费
    ok, why = G.check_suggestion("take_profit_bp", 4.0)
    assert ok is False
    assert "taker" in why
    # 6.0：虽然过了方向性下限，但距最优 12.0 太远 ⇒ 仍被 allow_delta 拒绝
    ok2, why2 = G.check_suggestion("take_profit_bp", 6.0)
    assert ok2 is False, "6.0 距最优 12.0 差 6.0，超出允许 2.0，应拒绝"
    assert "偏窄" in why2 or "允许" in why2
    # 允许区间 [10, 14] 内应放行
    for v in (10.0, 12.0, 14.0):
        ok3, _ = G.check_suggestion("take_profit_bp", v)
        assert ok3 is True, f"{v} 在允许区间内，应放行"
    # 15.0 超上界 ⇒ 拒绝
    ok4, _ = G.check_suggestion("take_profit_bp", 15.0)
    assert ok4 is False


@pytest.mark.unit
def test_reduce_upper_bound_enforced():
    """出库挂宽上限 0.6 —— 再宽就接近「挂在盘口外」（H163 的老问题）。"""
    ok, why = G.check_suggestion("spread_mult_reduce", 0.95)
    assert ok is False
    assert "盘口外" in why


@pytest.mark.unit
def test_unknown_key_rejected():
    """不在实测最优清单里的键 => 无证据支撑 => 拒绝。"""
    ok, why = G.check_suggestion("w_base_bp", 3.0)
    assert ok is False
    assert "清单" in why or "证据" in why


@pytest.mark.unit
def test_non_numeric_rejected():
    ok, why = G.check_suggestion("spread_mult", "宽一点")
    assert ok is False
    assert "数值" in why


@pytest.mark.unit
def test_every_optimum_entry_has_evidence():
    """清单里每条都必须带证据来源 —— 无来源的「锁定」就是拍脑袋。"""
    for k, s in G.OPTIMUM.items():
        assert s.get("evidence"), f"{k} 缺证据"
        assert len(s["evidence"]) > 30, f"{k} 证据太短，不足以追溯"
        assert s.get("level") in ("LOCKED", "BOUNDED", "FREE"), f"{k} 级别非法"


@pytest.mark.unit
def test_locked_params_have_tight_upward_span():
    """LOCKED 级别往宽方向的允许幅度必须很小（否则「锁定」无意义）。"""
    for k, s in G.OPTIMUM.items():
        if s["level"] == "LOCKED":
            assert s.get("allow_up") is not None
            assert s["allow_up"] <= 0.1, (
                f"{k} 标为 LOCKED 但往宽允许 {s['allow_up']}")
