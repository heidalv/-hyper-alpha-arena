# -*- coding: utf-8 -*-
"""
T5 G1 验证：因子进化闭环硬门槛生效核验。

覆盖（v6 计划 阶段1 第2/3项 + 5.4.3 双防过拟合）：
  A. purge 管线 ≤50 正交化因子：
       - 完整管线（run_purge_pipeline）输出 ≤ max_active_factors(50)
       - 存活因子两两相关受控（stage6 增量相关 ≤ 0.50）
       - stage6 增量相关 > 0.50 拒绝（正交化纪律）
       - stage2 数值近重复（corr>0.95）去重
       - stage7 DSR/PBO 门计数接线
  B. DSR/PBO 硬门槛：
       - ORTHO→PAPER：dsr_significant=False 拦截
       - ORTHO→PAPER：pbo>0.50 拦截
       - ORTHO→PAPER：incremental_corr>0.50 拦截
       - 全部达标 → 晋升 PAPER（auto=True）
       - _auto_oversight_approve 复核比基础门槛更严（pbo≤0.30）
       - compute_dsr_pbo_for_factors 整体门：低质量集不通过、高质量集通过
       - DSR 多重检验校正：n_trials 增大 p 值显著恶化

运行：.venv\\Scripts\\python.exe -m pytest backend\\tests\\unit\\test_g1_evolution_gates.py -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services.factor_engine.dsr_pbo import (
    compute_dsr,
    compute_dsr_pbo_for_factors,
    compute_pbo_simple,
)
from backend.services.factor_engine.evaluation import FactorEvalResult
from backend.services.factor_engine.lifecycle import (
    FactorMetrics,
    FactorState,
    LifecycleThresholds,
    TransitionDecision,
    evaluate_transition,
)
from backend.services.factor_engine.purge_pipeline import (
    CandidateFactor,
    PurgeConfig,
    run_purge_pipeline,
    stage2_dedup,
    stage6_pool_select,
)
from backend.services.evolution.factor_evolution_loop import _auto_oversight_approve


# ═══════════════════════════════════════════════════════════════════
# 数据构造
# ═══════════════════════════════════════════════════════════════════

def _make_series(n: int = 400, seed: int = 7):
    """构造 (returns, forward_returns_5h, index)。fwd = 未来 5 期收益。"""
    rng = np.random.default_rng(seed)
    rets = rng.normal(0, 0.01, n)
    fwd = np.zeros(n)
    fwd[:-5] = rets[5:]
    idx = pd.date_range("2026-01-01", periods=n, freq="4h")
    return pd.Series(rets, index=idx), pd.Series(fwd, index=idx)


def _mk_ast(i: int) -> dict:
    """合法但互不相同的表达式 AST（过 stage1 静态审计 + hash 去重键不同）。"""
    return {"op": "add", "args": [{"f": "close"}, {"c": float(i)}]}


def _candidate_series(fwd: pd.Series, seed: int, u_std: float) -> pd.Series:
    """候选因子序列：与远期收益相关受控（u_std 越小相关越高）。

    base = fwd×5（std≈0.05）作为共享信号，叠加独立噪声 u~N(0, u_std)：
      - u_std=0.01  → 相关 ≈ 0.96（IC 极高，稳过 CPCV 初筛）
      - u_std=0.062 → 相关 ≈ 0.40（过初筛，且候选间相关 ≤0.50 进池）
    所有档位单调性 p 极小、turnover≈0.5、IC 半衰期充足 → 初筛全过。
    """
    rng = np.random.default_rng(seed)
    base = fwd.values * 5.0
    return pd.Series(base + rng.normal(0, u_std, len(base)), index=fwd.index)


# ═══════════════════════════════════════════════════════════════════
# A. purge ≤50 正交化因子
# ═══════════════════════════════════════════════════════════════════

def test_purge_caps_active_at_50():
    """完整管线：80 个候选（均可过初筛）→ 存活 ≤ 50，报告计数正确。"""
    rets, fwd = _make_series()
    n = 80
    cands = [
        CandidateFactor(factor_id=f"f{i:03d}", source_name=f"src{i}",
                        expr_ast=_mk_ast(i))
        for i in range(n)
    ]
    series_map = {
        f"f{i:03d}": _candidate_series(fwd, seed=1000 + i, u_std=0.01)
        for i in range(n)
    }

    def factor_series_fn(c: CandidateFactor) -> pd.Series:
        return series_map.get(c.factor_id, pd.Series())

    survivors, report = run_purge_pipeline(
        cands,
        factor_series_fn=factor_series_fn,
        return_series=fwd,
        config=PurgeConfig(max_active_factors=50),
        thresholds=LifecycleThresholds(),
    )

    assert report.total_input == n
    assert report.rejected_static == 0
    assert len(survivors) <= 50, f"存活 {len(survivors)} > 50（max_active_factors 未生效）"
    assert report.surviving == len(survivors)
    # 超量输入（80）确实触发了池筛容量拒绝（部分候选因初筛随机未达标被提前拒，
    # 但只要存在池筛拒绝即证明 max_active_factors 真实生效，而非输入恰好 ≤50）
    assert report.rejected_pool >= 1, "超容量的候选未被池筛拒绝（容量限制未生效）"


def test_purge_survivors_mutual_corr_bounded():
    """中等相关输入（corr≈0.40）：全部存活且两两相关受控，高相关副本被拒。"""
    rets, fwd = _make_series()
    n = 10
    cands = [
        CandidateFactor(factor_id=f"f{i:02d}", source_name=f"src{i}",
                        expr_ast=_mk_ast(i))
        for i in range(n)
    ]
    series_map = {
        f"f{i:02d}": _candidate_series(fwd, seed=2000 + i, u_std=0.062)
        for i in range(n)
    }

    # 3 个高相关副本（与 f00 近重复，corr≈1.0）→ 池筛必须拒绝
    rng = np.random.default_rng(888)
    for j in range(3):
        fid = f"dup{j}"
        vals = series_map["f00"].values + rng.normal(0, 1e-4, len(fwd))
        cands.append(CandidateFactor(factor_id=fid, source_name=fid,
                                     expr_ast=_mk_ast(100 + j)))
        series_map[fid] = pd.Series(vals, index=fwd.index)

    def factor_series_fn(c: CandidateFactor) -> pd.Series:
        return series_map.get(c.factor_id, pd.Series())

    survivors, report = run_purge_pipeline(
        cands,
        factor_series_fn=factor_series_fn,
        return_series=fwd,
        config=PurgeConfig(max_active_factors=50),
        thresholds=LifecycleThresholds(),
    )

    assert report.rejected_eval == 0, "构造的候选应全部通过 CPCV 初筛"
    assert len(survivors) == n, "中等相关候选应全部进池"
    assert report.rejected_pool == 3, "高相关副本应被增量相关闸拒绝"
    # 存活因子两两相关受控（池筛保证增量相关 ≤0.50，数值容差 0.01）
    max_corr = 0.0
    ids = [s.factor_id for s in survivors]
    for i, a_id in enumerate(ids):
        a = series_map[a_id].values
        for b_id in ids[i + 1:]:
            b = series_map[b_id].values
            common = np.isfinite(a) & np.isfinite(b)
            if common.sum() < 10:
                continue
            corr = abs(np.corrcoef(a[common], b[common])[0, 1])
            max_corr = max(max_corr, corr)
    assert max_corr <= 0.51, f"存活因子最大两两相关 {max_corr:.3f} > 0.50"
    assert all(s.incremental_corr <= 0.50 + 1e-6 for s in survivors)


def test_purge_pool_incremental_corr_050_rejects():
    """stage6：增量相关 >0.50 拒绝、≤0.50 接纳（贪心按 ICIR 降序）。"""
    rng = np.random.default_rng(42)
    base = rng.normal(0, 1.0, 400)
    # 高相关候选：base + 1% 噪声 → corr≈0.995 > 0.50
    hi = base + rng.normal(0, 0.01, 400) * np.std(base) * 0.1
    # 低相关候选：独立序列 → corr≈0 ≤ 0.50
    lo = rng.normal(0, 1.0, 400)

    def mk(fid, vals, icir):
        c = CandidateFactor(factor_id=fid, source_name=fid, expr_ast=_mk_ast(hash(fid) % 10000))
        c.eval_result = FactorEvalResult(factor_id=fid, icir=icir)
        return c, pd.Series(vals)

    hi_c, hi_s = mk("hi", hi, 0.9)
    lo_c, lo_s = mk("lo", lo, 0.5)
    series_map = {"hi": hi_s, "lo": lo_s}
    config = PurgeConfig(pool_incremental_corr_max=0.50)

    pool, rejected = stage6_pool_select(
        [hi_c, lo_c],
        factor_series_fn=lambda c: series_map[c.factor_id],
        return_series=pd.Series(rng.normal(0, 0.01, 400)),
        config=config,
    )

    # hi 先进池（ICIR 0.9 更高）→ lo 与池内相关≈0 通过 → 都存活但相关受控
    assert all(c.status == "ACTIVE" for c in pool)
    # 单独测 hi 对 hi 副本：第二个 hi 应被拒
    hi_c2, hi_s2 = mk("hi2", hi, 0.8)
    series_map["hi2"] = hi_s2
    pool2, rej2 = stage6_pool_select(
        [hi_c, hi_c2],
        factor_series_fn=lambda c: series_map[c.factor_id],
        return_series=pd.Series(rng.normal(0, 0.01, 400)),
        config=config,
    )
    assert len(pool2) == 1, "增量相关>0.50 的候选未被拒绝"
    assert len(rej2) == 1
    assert "增量相关" in rej2[0].reject_reason


def test_purge_dedup_corr_095_rejects_near_duplicates():
    """stage2：数值近重复（corr>0.95）去重，每组只留一个。"""
    rng = np.random.default_rng(9)
    base = rng.normal(0, 1.0, 500)
    cands, series_map = [], {}
    for i in range(5):
        noise = rng.normal(0, 0.001, 500)  # 极小噪声 → corr≈1.0 > 0.95
        vals = base + noise
        fid = f"dup{i}"
        cands.append(CandidateFactor(factor_id=fid, source_name=fid,
                                     expr_ast=_mk_ast(i)))
        series_map[fid] = vals

    surviving, rejected = stage2_dedup(
        cands, PurgeConfig(dedup_corr_threshold=0.95),
        eval_fn=lambda c: series_map[c.factor_id],
    )

    assert len(surviving) == 1, "近重复族应只保留 1 个"
    assert len(rejected) == 4
    assert all("近重复" in r.reject_reason for r in rejected)


def test_purge_static_audit_rejects_none_ast():
    """stage1：无法转译为 AST 的候选（expr_ast=None）被静态审计拒绝。"""
    from backend.services.factor_engine.purge_pipeline import stage1_static_audit

    good = CandidateFactor(factor_id="g", source_name="g", expr_ast=_mk_ast(1))
    bad = CandidateFactor(factor_id="b", source_name="b", expr_ast=None)
    surv, rej = stage1_static_audit([good, bad])
    assert surv == [good]
    assert rej == [bad]
    assert "无法转译" in rej[0].reject_reason


def test_purge_dsr_pbo_gate_counts_rejected():
    """stage7：DSR/PBO 门接线——门收到的候选全部拒绝，计数进报告。"""
    rets, fwd = _make_series()
    cands = [
        CandidateFactor(factor_id=f"f{i:03d}", source_name=f"src{i}",
                        expr_ast=_mk_ast(i))
        for i in range(5)
    ]
    series_map = {f"f{i:03d}": _candidate_series(fwd, seed=3000 + i, u_std=0.062) for i in range(5)}

    received: list[CandidateFactor] = []

    def gate(cands_in):
        received.extend(cands_in)
        return [], list(cands_in)  # 模拟 DSR/PBO 全拒

    survivors, report = run_purge_pipeline(
        cands,
        factor_series_fn=lambda c: series_map[c.factor_id],
        return_series=fwd,
        config=PurgeConfig(max_active_factors=50),
        thresholds=LifecycleThresholds(),
        dsr_pbo_gate=gate,
    )
    # 门在所有通过池筛的候选上执行（初筛可能拒掉一部分，门只看到幸存者）
    assert survivors == []
    assert len(received) >= 1
    assert report.rejected_dsr_pbo == len(received), "stage7 拒绝计数与门输出不一致"


# ═══════════════════════════════════════════════════════════════════
# B. DSR/PBO 硬门槛（ORTHO → PAPER 状态机）
# ═══════════════════════════════════════════════════════════════════

def _ortho_metrics(**overrides) -> FactorMetrics:
    base = dict(
        factor_id="f_ortho",
        state=FactorState.ORTHO,
        audit_passed=True,
        icir=0.5,
        monotonicity_p=0.01,
        turnover=0.3,
        halflife_bars=20,
        incremental_corr=0.1,
        dsr_significant=True,
        pbo=0.2,
        capacity_usd=1e6,
    )
    base.update(overrides)
    return FactorMetrics(**base)


def test_dsr_insignificant_blocks_ortho_to_paper():
    """DSR 不显著 → ORTHO 不晋升 PAPER（硬门槛）。"""
    d = evaluate_transition(_ortho_metrics(dsr_significant=False))
    assert d.to_state == FactorState.ORTHO, "dsr_significant=False 仍晋升了"
    assert not d.auto
    assert "dsr_significant" in d.conditions_failed


def test_pbo_high_blocks_ortho_to_paper():
    """PBO > 0.50 → 拦截晋升（多重检验过拟合概率过高）。"""
    d = evaluate_transition(_ortho_metrics(pbo=0.9))
    assert d.to_state == FactorState.ORTHO
    assert not d.auto
    assert "pbo" in d.conditions_failed


def test_incremental_corr_high_blocks_ortho_to_paper():
    """对池增量相关 > 0.50 → 拦截（正交化纪律）。"""
    d = evaluate_transition(_ortho_metrics(incremental_corr=0.9))
    assert d.to_state == FactorState.ORTHO
    assert not d.auto
    assert "incremental_corr" in d.conditions_failed


def test_capacity_low_blocks_ortho_to_paper():
    """容量 < 1e5 USD → 拦截（流动性门槛）。"""
    d = evaluate_transition(_ortho_metrics(capacity_usd=1e3))
    assert d.to_state == FactorState.ORTHO
    assert "capacity_usd" in d.conditions_failed


def test_all_gates_pass_promotes_to_paper():
    """全部达标（dsr 显著 + pbo≤0.5 + 相关≤0.5 + 容量够）→ 晋升 PAPER。"""
    d = evaluate_transition(_ortho_metrics())
    assert d.to_state == FactorState.PAPER
    assert d.auto
    assert not d.conditions_failed


def test_auto_oversight_stricter_than_base_gates():
    """自动化复核（_auto_oversight_approve）比基础门槛更严：pbo≤0.30。"""
    from types import SimpleNamespace

    def judge_for(to_state):
        return SimpleNamespace(decision=TransitionDecision(
            "f", FactorState.ORTHO, to_state, auto=True, reason="x"))

    m_pbo04 = _ortho_metrics(pbo=0.4, paper_sharpe=2.0, paper_days=20,
                             small_live_days=30)
    # pbo=0.40 通过基础门槛（≤0.50）但复核拒绝（>0.30）
    assert not _auto_oversight_approve(m_pbo04, judge_for(FactorState.SMALL_LIVE))
    # pbo=0.20 + 纸面达标（sharpe≥1.5×1.0、days≥2×5）→ 复核通过
    m_ok = _ortho_metrics(pbo=0.2, paper_sharpe=1.8, paper_days=12,
                          small_live_days=30)
    assert _auto_oversight_approve(m_ok, judge_for(FactorState.SMALL_LIVE))
    # ACTIVE 复核需要小仓期 ≥ 1.5×14
    m_active_ok = _ortho_metrics(pbo=0.2, paper_sharpe=1.8, paper_days=12,
                                 small_live_days=25)
    assert _auto_oversight_approve(m_active_ok, judge_for(FactorState.ACTIVE))
    m_active_short = _ortho_metrics(pbo=0.2, paper_sharpe=1.8, paper_days=12,
                                    small_live_days=10)
    assert not _auto_oversight_approve(m_active_short, judge_for(FactorState.ACTIVE))


def test_dsr_n_trials_multiple_testing_penalty():
    """DSR 多重检验校正：试验数越多，同样 SR 的显著性越差。"""
    r1 = compute_dsr(observed_sr=2.0, n_trials=1)
    r_big = compute_dsr(observed_sr=2.0, n_trials=5000)
    assert r1["significant"], "单次试验 SR=2.0 应显著"
    assert not r_big["significant"], "5000 次试验下 SR=2.0 应被多重检验校正打掉"
    assert r_big["p_value"] > r1["p_value"]


def test_compute_dsr_pbo_for_factors_gates_quality():
    """整体门：低质量因子集不通过，高质量因子集通过（DSR 显著 + PBO<0.5）。"""
    low = compute_dsr_pbo_for_factors(
        icir_list=[0.3, 0.2, 0.1, 0.0, -0.1, -0.2, -0.3, -0.4, -0.5, -0.6],
        n_total_candidates=10,
        sample_len=252,
    )
    assert low["overall_passes"] is False
    assert low["dsr_result"]["significant"] is False

    high = compute_dsr_pbo_for_factors(
        icir_list=[3.0] + [0.5] * 19,
        n_total_candidates=20,
        sample_len=252,
    )
    assert high["dsr_result"]["significant"] is True
    assert high["pbo_result"]["pbo"] < 0.5
    assert high["overall_passes"] is True


def test_pbo_direction_semantics_is_academic():
    """PBO 方向语义（2026-08-05 修复）：IS 最优因子在 OOS 排名靠后才算过拟合。

    10 个递减 ICIR（2.0→1.1，C(5,2)=10 组全枚举）：
      修复前（r≤N/2 判 overfit）：7/10 组合 overfit → pbo=0.7
      修复后（r>N/2 判 overfit）：3/10 组合 overfit → pbo=0.3
    只有 IS 最优在 OOS 中位排名之外（表现差）才累计 PBO。
    """
    res = compute_pbo_simple(icir_values=[2.0, 1.9, 1.8, 1.7, 1.6,
                                          1.5, 1.4, 1.3, 1.2, 1.1])
    assert res["pbo"] == 0.3, f"方向语义错误：pbo={res['pbo']}（期望 0.3）"
    assert res["significant"] is True


def test_pbo_stable_best_in_oos_not_overfit():
    """全局最优因子在 OOS 仍保持第一 → 不判过拟合（修复后 pbo=0）。"""
    res = compute_pbo_simple(icir_values=[3.0] + [0.5] * 19)
    assert res["pbo"] == 0.0, f"稳定最优因子被误判过拟合：pbo={res['pbo']}"
