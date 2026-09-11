# -*- coding: utf-8 -*-
"""[2026-09-10 第二十一轮] 「先盈利后大亏」审计脚本聚合逻辑契约测试。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.scripts.audit_profit_giveback import (  # noqa: E402
    source_family, summarize, total_usd,
)


def _row(tier, sym, peak_pct, usd, notional=100.0, reason="x", allow=None, fam=None):
    row = {"tier": tier, "symbol": sym, "peak_pnl_pct": peak_pct / 100.0,
           "notional0": notional, "usd": usd, "close_reason": reason}
    if allow is not None:
        row["allow"] = allow
    if fam is not None:
        row["family"] = fam
    return row


def test_total_usd_net_of_partial_and_fees():
    row = {"unrealized_pnl": -3.0, "partial_realized_pnl": 5.0, "partial_fee_paid": 0.5}
    assert abs(total_usd(row) - 1.5) < 1e-9
    assert total_usd({}) == 0.0


def test_summarize_pattern_and_big_loss():
    rows = [
        _row("mid", "A", 1.5, -10.0),      # 模式 + 大亏(10% of 100)
        _row("mid", "B", 0.2, -3.0),       # 峰值不足 → 非模式
        _row("mid", "C", 2.0, +8.0),       # 模式但盈利 → 非模式
        _row("long", "D", 0.8, -2.5, notional=200.0),  # 模式，但 1.25% → 非大亏
    ]
    rep = summarize(rows)
    assert rep["pattern_n"] == 2
    assert abs(rep["pattern_usd"] - (-12.5)) < 1e-9
    mid = rep["by_tier"]["mid"]
    # A(-10%) 与 B(-3%) 都算大亏；C 盈利
    assert mid["n"] == 3 and mid["pattern_n"] == 1 and mid["big_loss_n"] == 2
    assert abs(mid["win_rate"] - round(1 / 3, 3)) < 1e-9
    long_ = rep["by_tier"]["long"]
    assert long_["pattern_n"] == 1 and long_["big_loss_n"] == 0
    # worst5 按 USD 升序
    assert rep["worst5"][0]["symbol"] == "A"


def test_summarize_empty():
    rep = summarize([])
    assert rep["pattern_n"] == 0 and rep["pattern_usd"] == 0.0
    assert rep["by_tier"] == {} and rep["worst5"] == []


# ---------- 第二十三轮新增：回吐严重度 + 门判别 + 判定线 ----------

def test_giveback_severity_is_peak_minus_final_pct():
    """回吐 = 峰值% − 最终%（最终用 总USD/名义 口径）。"""
    rows = [
        _row("mid", "A", 3.0, -4.5),   # 峰值 3.0%，最终 -4.5% → 回吐 7.5%
        _row("mid", "B", 1.0, -1.0),   # 回吐 2.0%
        _row("mid", "C", 2.0, +5.0),   # 盈利 → 不计入模式
    ]
    mid = summarize(rows)["by_tier"]["mid"]
    assert mid["pattern_n"] == 2
    assert abs(mid["giveback_pct_sum"] - 9.5) < 1e-6
    # 偶数个时取上中位（[2.0, 7.5] → 7.5）
    assert mid["median_giveback_pct"] == 7.5


def test_gate_split_separates_pattern_flow():
    """门放行 / 门拦截 的模式率与 USD 应分别统计。"""
    rows = [
        _row("mid", "A", 1.0, -5.0, allow=True),    # 放行·模式
        _row("mid", "B", 0.1, -1.0, allow=True),    # 放行·非模式
        _row("mid", "C", 2.0, -8.0, allow=False),   # 拦截·模式
        _row("mid", "D", 1.5, -9.0, allow=False),   # 拦截·模式
    ]
    rep = summarize(rows)
    g = rep["gate"]
    assert g["allow"]["n"] == 2 and g["allow"]["pattern_n"] == 1
    assert g["block"]["n"] == 2 and g["block"]["pattern_n"] == 2
    assert g["allow"]["pattern_rate"] == 0.5 and g["block"]["pattern_rate"] == 1.0
    assert abs(g["block"]["pattern_usd"] - (-17.0)) < 1e-9
    ms = rep["by_tier"]["mid"]["gate_split"]
    assert ms["allow"]["n"] == 2 and ms["block"]["n"] == 2


def test_verdicts_flag_gate_discrimination_and_long_exemption():
    """判定线：门放行模式率更低 + long 车道反事实被拦集更赚 → 两条都达标。"""
    rows = [
        _row("mid", "A", 1.0, -5.0, allow=True),
        _row("mid", "F", 0.1, +3.0, allow=True),      # 放行·非模式（拉低放行模式率）
        _row("mid", "B", 2.0, -9.0, allow=False),
        _row("mid", "C", 2.5, -7.0, allow=False),
        _row("long", "D", 0.9, +20.0, allow=False),   # 反事实被拦，但很赚
        _row("long", "E", 0.1, -2.0, allow=True),    # 放行·非模式
    ]
    rep = summarize(rows)
    by_name = {v["name"]: v for v in rep["verdicts"]}
    assert by_name["门放行集模式率 < 门拦截集"]["ok"] is True
    assert by_name["门放行集模式 USD/笔 优于门拦截集"]["ok"] is True
    assert by_name["long 车道豁免正确（反事实：被拦集更赚）"]["ok"] is True
    # mid 模式 USD 为负 → 该条应判未达标
    assert by_name["mid 模式 USD ≥ 0"]["ok"] is False


def test_verdicts_without_gate_flags_skip_gate_lines():
    """未做门判定（--no-gate）时不应产生门相关判定线，且不得报错。"""
    rep = summarize([_row("mid", "A", 1.0, -5.0)])
    names = [v["name"] for v in rep["verdicts"]]
    assert not any("门放行集" in n for n in names)
    assert "mid 模式 USD ≥ 0" in names


# ---------- 第二十四轮新增：入场来源家族 ----------

def test_source_family_mapping():
    """strategy_id → 来源家族：前缀匹配、最长优先、未知归 other。"""
    assert source_family("tpl_mid_reversion_43da65") == "mid_reversion"
    assert source_family("tpl_mid_range_4a7068") == "mid_range"
    assert source_family("tpl_long_mean_reversion_a8bcf0") == "long_reversion"
    assert source_family("tpl_long_swing_d8913c") == "long_swing"
    assert source_family("gen_auto_mean_reversion_eth_blend_v1_327") == "gen"
    assert source_family("auto_80cba52144") == "auto"
    assert source_family("trend_e1:LINK") == "trend_e1"
    assert source_family("tpl_pro_075b93de_59a768") == "pro"
    assert source_family("") == "other"
    assert source_family(None) == "other"
    assert source_family("weird_id") == "other"


def test_by_family_aggregation():
    """来源家族维度的 n/USD/模式率应分别统计。"""
    rows = [
        _row("mid", "A", 1.0, -5.0, fam="mid_reversion"),   # 模式
        _row("mid", "B", 0.1, -1.0, fam="mid_reversion"),   # 非模式
        _row("mid", "C", 2.0, +9.0, fam="mid_reversion"),   # 盈利
        _row("mid", "D", 1.5, -8.0, fam="pro"),             # 模式
    ]
    rep = summarize(rows)
    fam = rep["by_family"]
    assert fam["mid_reversion"]["n"] == 3
    assert fam["mid_reversion"]["pattern_n"] == 1
    assert abs(fam["mid_reversion"]["pattern_rate"] - round(1 / 3, 3)) < 1e-9
    assert abs(fam["mid_reversion"]["usd"] - (-5.0 - 1.0 + 9.0)) < 1e-9
    assert fam["pro"]["n"] == 1 and fam["pro"]["pattern_rate"] == 1.0
    # 无 family 字段时不产出该键（保持向后兼容）
    assert "by_family" not in summarize([_row("mid", "X", 1.0, -5.0)])
