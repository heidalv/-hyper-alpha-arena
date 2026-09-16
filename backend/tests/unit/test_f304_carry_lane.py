# -*- coding: utf-8 -*-
"""[F304 2026-09-16] carry 测量模块契约测试（**单所前提下该车道不成立**）。

背景：`carry_basis` 此前**只有一份自相矛盾的回测数字**（gross 5.15 − cost 28.67
≠ net +33.81），没有任何可运行的 tick 代码。补做测量后结论是：
**carry 的机制必然是跨所的，单所做不了**（用户已明确只做单所）⇒ 车道保持
`stopped`，模块不在运行路径上。

因此本测试锁定两组东西：
  A. **必须拒绝运行**——`tick()` 恒返回 disabled，且不得被任何 ticker/runner 引用。
     这比"能跑"更重要：防止有人误挂一个 ticker 把已知不成立的车道跑起来。
  B. 若将来真开了跨所通道，测量内核（摩擦/窗口/闸门/显著性）必须仍然正确。
"""
from __future__ import annotations

import inspect

import pytest

from backend.services import carry_lane as C

WIN = C.WIN_MS


# ── A. 不在运行路径上（最重要的护栏）────────────────────────────────────
def test_tick_refuses_to_run_under_single_venue():
    """单所前提下 carry 不成立 ⇒ tick 必须直接拒绝，而不是"跑起来但不建仓"。"""
    res = C.tick("carry_basis")
    assert res["ok"] is False
    assert res.get("disabled") is True
    assert "单所" in res["reason"]


def test_no_ticker_or_runner_imports_carry_lane():
    """模块不得被任何 worker / runner / scripts 引用（否则会被跑起来）。"""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[3]
    hits = []
    for sub in ("backend/workers", "backend/services/market_maker",
                "backend/api", "scripts"):
        d = root / sub
        if not d.exists():
            continue
        for p in d.rglob("*.py"):
            try:
                t = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if "carry_lane" in t:
                hits.append(str(p.relative_to(root)))
    assert not hits, f"carry_lane 被这些文件引用（会把它跑起来）：{hits}"


def test_module_does_not_self_register_or_change_status():
    src = inspect.getsource(C)
    assert "register_lane" not in src, "模块不得自行注册车道"
    assert "set_status" not in src, "模块不得自行改车道状态"


def test_no_spot_leg_in_friction_model():
    """本项目没有现货通道 ⇒ 摩擦模型不得出现现货腿费率。"""
    src = inspect.getsource(C)
    assert "spot_taker" not in src
    assert C.FRICTION_BP["aster_maker"] == 0.0, "Aster 永续 maker 实测 0bp"


# ── B. 测量内核（将来开跨所通道时仍必须正确）────────────────────────────
def test_round_trip_cost_values():
    ideal = C.round_trip_cost_bp("maker", "maker")
    realistic = C.round_trip_cost_bp("maker", "taker")
    worst = C.round_trip_cost_bp("taker", "taker")
    assert ideal == pytest.approx(9.0)       # (0+2 + 2+0.5) × 2
    assert realistic == pytest.approx(14.0)  # (0+2 + 4.5+0.5) × 2
    assert worst == pytest.approx(22.0)      # (4+2 + 4.5+0.5) × 2
    assert ideal < realistic < worst, "全腿 taker 必须最贵"


def test_cost_is_charged_twice_entry_and_exit():
    """只算一次会把成本低估一半（旧回测正是把单边当往返）。"""
    one_side = (C.FRICTION_BP["aster_maker"] + C.FRICTION_BP["aster_slip"]
                + C.FRICTION_BP["binance_taker"] + C.FRICTION_BP["binance_slip"])
    assert C.round_trip_cost_bp("maker", "taker") == pytest.approx(one_side * 2)


def test_bucket_takes_last_observation_in_window():
    """窗口内预测值持续漂移；结算生效的是最后那个值。"""
    obs = [(0, 0.00001), (WIN // 2, 0.00003), (WIN - 1, 0.00009)]
    b = C.bucket_settlements(obs)
    assert len(b) == 1
    assert b[0][1] == pytest.approx(0.00009), "必须取桶内最后一条"


def test_bucket_splits_at_window_boundary():
    obs = [(WIN - 1, 0.00001), (WIN, 0.00002), (2 * WIN, 0.00003)]
    b = C.bucket_settlements(obs)
    assert sorted(b) == [0, 1, 2]


def test_bucket_out_of_order_input():
    obs = [(WIN - 1, 0.00001), (1, 0.00005)]
    b = C.bucket_settlements(obs)
    assert b[0][1] == pytest.approx(0.00001)


def test_bucket_empty():
    assert C.bucket_settlements([]) == {}


def test_align_drops_windows_missing_on_either_side():
    """窗口只有单边时必须丢弃——否则是拿不同期相减 ⇒ 假差价。"""
    a = {1: (WIN, 0.0001), 2: (2 * WIN, 0.0002), 3: (3 * WIN, 0.0003)}
    b = {1: (WIN, 0.00005), 3: (3 * WIN, 0.0001)}
    wins = C.align_windows(a, b)
    assert [w.window for w in wins] == [1, 3]


def test_align_converts_to_bp_and_subtracts():
    a = {1: (WIN, 0.0002)}          # +2.0 bp
    b = {1: (WIN, 0.00005)}         # +0.5 bp
    (w,) = C.align_windows(a, b)
    assert w.rate_aster_bp == pytest.approx(2.0)
    assert w.rate_binance_bp == pytest.approx(0.5)
    assert w.diff_bp == pytest.approx(1.5)


def test_align_sorted_by_window():
    a = {3: (3 * WIN, 0.0), 1: (WIN, 0.0)}
    b = {3: (3 * WIN, 0.0), 1: (WIN, 0.0)}
    assert [w.window for w in C.align_windows(a, b)] == [1, 3]


# ── 闸门 ───────────────────────────────────────────────────────────────
PARAMS = C.CarryParams(
    enabled=True, min_edge_bp=2.0, max_hold_days=1.0, min_hold_days=1.0,
    persist_windows=6, persist_min_frac=0.5, venue_risk_bp=3.0,
    aster_mode="maker", binance_mode="taker", max_stale_hours=12.0,
    require_significance=False,
)


def _wins(diffs_bp, start=1000):
    """构造窗口序列，diff（Aster − Binance）**按每结算期 bp** 给定。

    量纲提醒：`decide` 的输入是**每结算期**的差价，不是每天。
    `carry_bp_per_day = |mean| × 3`（8h 结算 ⇒ 3 次/天）。
    早先我把夹具按 bp/天 喂，投影被乘了 3 倍、测试全错——量纲必须显式。
    """
    a, b = {}, {}
    for i, d in enumerate(diffs_bp):
        w = start + i
        a[w] = (w * WIN, 0.0)
        b[w] = (w * WIN, -d / 1e4)
    return C.align_windows(a, b)


def _per_day(x):
    return x / C.SETTLE_PER_DAY


def test_default_params_disable_entry():
    """默认必须是不建仓——这是回滚开关，不能默认开启。"""
    assert C.CarryParams().enabled is False


def test_disabled_lane_never_enters():
    ms = C.decide("ARB", _wins([_per_day(27.0)] * 6), C.CarryParams(
        enabled=False, max_hold_days=1.0, min_hold_days=1.0))
    assert ms.decision == "flat"
    assert "lane_disabled" in ms.reason


def test_insufficient_windows_fails_closed():
    ms = C.decide("ARB", _wins([_per_day(27.0)] * 3), PARAMS)
    assert ms.decision == "flat"
    assert ms.reason == "insufficient_windows"


def test_edge_below_min_blocks_entry():
    """9 bp/天 × 1 天 = 9.0；减摩擦 14、场所风险 3 ⇒ 净 −8 ⇒ 拦。"""
    ms = C.decide("ARB", _wins([_per_day(9.0)] * 6), PARAMS)
    assert ms.decision == "flat"
    assert "edge_below_min" in ms.reason
    assert ms.projected_net_bp == pytest.approx(-8.0)


def test_realistic_carry_is_blocked_by_friction():
    """ARB 实测 2.17 bp/天，7 天持有 = 15.2 bp 毛；摩擦 14 + 场所风险 3 = 17
    ⇒ 净 −1.8 ⇒ 应拦。这正是"carry 边缘真实但覆盖不了摩擦"的量化结论。"""
    p7 = C.CarryParams(**{**PARAMS.__dict__, "max_hold_days": 7.0,
                          "min_hold_days": 7.0})
    ms = C.decide("ARB", _wins([_per_day(2.17)] * 20), p7)
    assert ms.projected_gross_bp == pytest.approx(2.17 * 7.0, rel=1e-3)
    assert ms.projected_net_bp < 0
    assert ms.decision == "flat"


def test_huge_edge_clears_friction():
    """12 bp/天 × 7 天 = 84 毛；84 − 17 = 67 净 ≥ 2 ⇒ 过。"""
    p7 = C.CarryParams(**{**PARAMS.__dict__, "max_hold_days": 7.0,
                          "min_hold_days": 7.0})
    ms = C.decide("X", _wins([_per_day(12.0)] * 20), p7)
    assert ms.decision == "would_enter"
    assert ms.projected_net_bp == pytest.approx(67.0)


def test_single_spike_does_not_enter_when_not_persistent():
    """ZEC 实测 mean 0.09 / sd 0.93：单期尖峰不得放行。"""
    ms = C.decide("ZEC", _wins([200.0, -1.0, -1.0, -1.0, -1.0, -1.0]), PARAMS)
    assert ms.decision == "flat"
    assert "not_persistent" in ms.reason


def test_significance_gate_blocks_noisy_positive_mean():
    """均值虽为正，但噪声大到置信下界没过盈亏平衡 ⇒ 必须拦（XMR 实测形状）。

    XMR：mean 1.017 bp/期、sd 2.065、40 期 ⇒ se 0.327
    ⇒ LCB = 1.017 − 1.645×0.327 = 0.48；7 天持有盈亏平衡 0.905 ⇒ 拦。
    """
    p7 = C.CarryParams(**{**PARAMS.__dict__, "max_hold_days": 7.0,
                          "min_hold_days": 7.0, "require_significance": True})
    seq = [1.017 + (2.065 if i % 2 else -2.065) for i in range(40)]
    ms = C.decide("XMR", _wins(seq), p7)
    assert ms.significant is False
    assert "not_significant" in ms.reason
    assert ms.decision == "flat"
    assert ms.mean_lcb_bp < ms.breakeven_per_settle_bp


def test_significance_gate_allows_tight_series():
    p7 = C.CarryParams(**{**PARAMS.__dict__, "max_hold_days": 7.0,
                          "min_hold_days": 7.0, "require_significance": True})
    ms = C.decide("X", _wins([12.0] * 40), p7)
    assert ms.significant is True
    assert ms.decision == "would_enter"


def test_significance_can_be_disabled_for_diagnostics():
    p7 = C.CarryParams(**{**PARAMS.__dict__, "max_hold_days": 7.0,
                          "min_hold_days": 7.0, "require_significance": False})
    ms = C.decide("XMR", _wins([1.017 + (2.065 if i % 2 else -2.065)
                                for i in range(40)]), p7)
    assert ms.significant is True, "关掉显著性时该字段视作通过（仅诊断用）"


def test_negative_mean_flips_side():
    ms = C.decide("X", _wins([_per_day(-40.0)] * 6), PARAMS)
    assert ms.decision == "would_enter"
    assert ms.side == "long_aster_short_binance", "方向必须由数据符号决定"


def test_stale_data_fails_closed():
    wins = _wins([_per_day(40.0)] * 6)
    now = 10_000 * WIN
    ms = C.decide("ARB", wins, PARAMS, last_obs_ms=now - 24 * 3_600_000, now_ms=now)
    assert ms.decision == "flat"
    assert ms.reason == "stale_data"
    assert ms.stale is True


def test_fresh_data_not_stale():
    wins = _wins([_per_day(40.0)] * 6)
    now = 10_000 * WIN
    ms = C.decide("ARB", wins, PARAMS, last_obs_ms=now - 3_600_000, now_ms=now)
    assert ms.stale is False
    assert ms.decision == "would_enter"


def test_carry_per_day_is_three_settlements():
    ms = C.decide("ARB", _wins([2.0] * 6), PARAMS)
    assert C.SETTLE_PER_DAY == 3.0
    assert ms.carry_bp_per_day == pytest.approx(2.0 * 3.0)


def test_venue_risk_is_subtracted():
    """场所尾部风险溢价必须真的从净收益里扣掉（Aster 是新场所）。"""
    p_no = C.CarryParams(**{**PARAMS.__dict__, "venue_risk_bp": 0.0})
    p_yes = C.CarryParams(**{**PARAMS.__dict__, "venue_risk_bp": 5.0})
    wins = _wins([_per_day(40.0)] * 6)
    a = C.decide("X", wins, p_no)
    b = C.decide("X", wins, p_yes)
    assert a.projected_net_bp - b.projected_net_bp == pytest.approx(5.0)


def test_measurement_to_dict_is_json_safe():
    ms = C.decide("ARB", _wins([_per_day(40.0)] * 6), PARAMS)
    d = ms.to_dict()
    assert "windows_detail" not in d, "to_dict 必须剔除明细（心跳文件不能膨胀）"
    import json
    json.dumps(d)


# ── edge 来源可信 ──────────────────────────────────────────────────────
def test_edge_source_is_trusted():
    from backend.services.lane_registry import EDGE_SOURCES

    ms = [C.decide("ARB", _wins([_per_day(40.0)] * 6), PARAMS) for _ in range(8)]
    edge = C.evaluate_edge(ms, PARAMS)
    assert edge["source"] in EDGE_SOURCES
    assert edge["n"] > 0
    assert len(edge["folds"]) >= 1
    for f in edge["folds"]:
        assert set(f) >= {"fold", "n", "net_bp", "t"}


def test_evaluate_edge_empty_when_no_measurements():
    assert C.evaluate_edge([], PARAMS) == {}


def test_promotion_recomputes_from_edge():
    from backend.services.lane_registry import evaluate_promotion

    ms = [C.decide("ARB", _wins([_per_day(40.0)] * 6), PARAMS) for _ in range(8)]
    edge = C.evaluate_edge(ms, PARAMS)
    pr = evaluate_promotion(edge)
    assert "edge_verified" in pr["passed"], "可信来源必须通过来源校验"
    assert pr["ready"] is False, "投影值不得直接晋级为 live"


def test_params_keys_match_dataclass():
    """参数白名单过滤按 dataclass 字段做；名字不一致会静默丢配置。"""
    fn = C.CarryParams.__dataclass_fields__
    for k in ("enabled", "min_edge_bp", "max_hold_days", "persist_windows",
              "venue_risk_bp", "aster_mode", "binance_mode", "leg_notional"):
        assert k in fn, f"参数 {k} 缺失"


def test_unknown_params_are_rejected_by_whitelist_filter():
    stored = {"enabled": True, "bogus_key": 123, "min_edge_bp": 5.0}
    p = C.CarryParams(**{k: v for k, v in stored.items()
                         if k in C.CarryParams.__dataclass_fields__})
    assert p.enabled is True and p.min_edge_bp == 5.0
    assert not hasattr(p, "bogus_key")
