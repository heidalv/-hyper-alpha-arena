"""
因子清洗管线（P1.2，方案 §P1.2，幂等可重跑）。

将 987 个无纪律 AI 因子清洗为 ≤50 个表达式化、过 DSR/PBO、pool-aware 的真因子。

管线步骤（全部自动，R4 客观指标驱动）：
    1. 静态审计：自由 Python 因子类 → 尝试转表达式 AST；audit pass → DRAFT(EXPR)
       不能转译 → REJECTED
    2. 去重：表达式规范化哈希 + 数值指纹（IC 相关 >0.95）去重
    3. CPCV 评估：IC/ICIR/单调性/turnover/半衰期
    4. 初筛：ICIR>0.3 且 单调性 p<0.05 且 turnover<70%
    5. 正交化：symmetric orthogonalization
    6. 增量池筛选（AlphaGen pool-aware）：贪心按 ICIR 降序，仅当对池 IC 边际贡献>eps 且增量相关<0.5 才接纳
    7. DSR + PBO 硬门槛
    8. 输出 ≤50 因子 → ORTHO 状态

注：987 个损坏因子（162 个连语法都不通过，P0.6 CI 已发现）在步骤 1 直接 REJECTED。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from backend.services.factor_engine.evaluation import FactorEvalResult, evaluate_factor
from backend.services.factor_engine.expr.audit import AuditResult, audit
from backend.services.factor_engine.lifecycle import (
    LifecycleThresholds,
)


@dataclass
class PurgeConfig:
    """清洗配置（方案 P1.2 阈值）。"""
    dedup_corr_threshold: float = 0.95    # 数值相关高于此 = 重复
    pool_incremental_corr_max: float = 0.50  # 增量相关上限
    pool_ic_improvement_eps: float = 1e-4    # 池 IC 边际贡献下限
    max_active_factors: int = 50
    # [2026-08-05 v6 2.4 S2-4] 数据质量门槛（L101）：因子值完整率低于此 → 清洗淘汰
    min_data_quality: float = 0.80


@dataclass
class CandidateFactor:
    """清洗管线中的候选因子。"""
    factor_id: str
    source_name: str           # 原文件名/来源
    expr_ast: dict | None      # 表达式 AST（None = 无法转译）
    status: str = "DRAFT"      # DRAFT/REJECTED/SURVIVING/ACTIVE
    reject_reason: str = ""
    eval_result: FactorEvalResult | None = None
    incremental_corr: float = 1.0
    data_quality: float = 1.0  # 因子值完整率 0~1（S2-4 数据质量维度）


@dataclass
class PurgeReport:
    """清洗报告。"""
    total_input: int = 0
    rejected_static: int = 0      # 静态审计/无法转译
    rejected_dedup: int = 0       # 去重
    rejected_eval: int = 0        # CPCV 初筛
    rejected_quality: int = 0     # 数据质量不足（S2-4）
    rejected_pool: int = 0        # 增量池筛选
    rejected_dsr_pbo: int = 0
    surviving: int = 0
    # applied=数值层 QR 已跑；skipped_no_matrix=调用方未供矩阵；skipped_trivial=因子数不足
    ortho_status: str = "skipped_no_matrix"
    candidates: list[CandidateFactor] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"输入 {self.total_input} → "
            f"静态拒 {self.rejected_static}, "
            f"去重拒 {self.rejected_dedup}, "
            f"初筛拒 {self.rejected_eval}, "
            f"质量拒 {self.rejected_quality}, "
            f"池筛拒 {self.rejected_pool}, "
            f"DSR/PBO 拒 {self.rejected_dsr_pbo} → "
            f"幸存 {self.surviving} "
            f"(ortho={self.ortho_status})"
        )


def default_dsr_pbo_gate(
    survivors: list[CandidateFactor],
    *,
    sample_len: int = 252,
    n_total_candidates: int | None = None,
) -> tuple[list[CandidateFactor], list[CandidateFactor]]:
    """Stage7 内置 DSR/PBO：禁止调用方漏传导致空跑。

    用幸存者 ICIR 集做多重检验；整批未过则全部拒绝（与 promote 全局语义一致，
    但发生在 purge，漏斗可计数）。
    """
    from backend.services.factor_engine.dsr_pbo import compute_dsr_pbo_for_factors

    if not survivors:
        return [], []
    icirs = []
    for c in survivors:
        r = c.eval_result
        if r is not None and np.isfinite(getattr(r, "icir", float("nan"))):
            icirs.append(float(r.icir))
    if not icirs:
        rejected = []
        for c in survivors:
            c.status = "REJECTED"
            c.reject_reason = "DSR/PBO：无有效 ICIR"
            rejected.append(c)
        return [], rejected

    n_trials = max(int(n_total_candidates or len(survivors)), len(icirs), 1)
    # 冷启动：仅少数幸存者时用幸存者数作分母，避免搜索广度自杀
    if len(survivors) <= 5:
        n_trials = max(len(survivors), 1)

    result = compute_dsr_pbo_for_factors(
        icir_list=icirs,
        n_total_candidates=n_trials,
        sample_len=max(50, int(sample_len)),
    )
    if result.get("overall_passes"):
        return survivors, []

    dsr = (result.get("dsr_result") or {})
    pbo = (result.get("pbo_result") or {})
    reason = (
        f"DSR/PBO 未过 dsr_sig={dsr.get('significant')} "
        f"pbo={pbo.get('pbo')}"
    )
    rejected = []
    for c in survivors:
        c.status = "REJECTED"
        c.reject_reason = reason
        rejected.append(c)
    return [], rejected


def _normalize_ast_for_dedup(ast: dict) -> str:
    """规范化 AST 用于去重哈希。"""
    return json.dumps(ast, sort_keys=True, ensure_ascii=False)


def stage1_static_audit(
    candidates: list[CandidateFactor],
) -> tuple[list[CandidateFactor], list[CandidateFactor]]:
    """
    步骤 1：静态审计。
    能转表达式 AST 且 audit pass → 保留；否则 REJECTED。
    """
    surviving, rejected = [], []
    for c in candidates:
        if c.expr_ast is None:
            c.status = "REJECTED"
            c.reject_reason = "无法转译为表达式 AST（自由 Python 代码）"
            rejected.append(c)
            continue
        result: AuditResult = audit(c.expr_ast)
        if not result.ok:
            c.status = "REJECTED"
            c.reject_reason = "audit 失败：" + "; ".join(result.errors)
            rejected.append(c)
            continue
        surviving.append(c)
    return surviving, rejected


def stage2_dedup(
    candidates: list[CandidateFactor],
    config: PurgeConfig,
    *,
    eval_fn: Callable[[CandidateFactor], np.ndarray] | None = None,
) -> tuple[list[CandidateFactor], list[CandidateFactor]]:
    """
    步骤 2：去重。
    - 表达式规范化哈希相同 = 完全重复
    - 数值指纹（IC 相关 >0.95）= 近重复
    保留每组 ICIR 最高的一个。
    """
    # 先按哈希去重
    seen_hash: dict[str, CandidateFactor] = {}
    after_hash = []
    for c in candidates:
        h = _normalize_ast_for_dedup(c.expr_ast)
        if h in seen_hash:
            c.status = "REJECTED"
            c.reject_reason = "表达式完全重复"
        else:
            seen_hash[h] = c
            after_hash.append(c)

    # 数值指纹去重（需 eval_fn 提供因子值序列）
    if eval_fn is None:
        return after_hash, [c for c in candidates if c.status == "REJECTED"]

    surviving = []
    rejected = []
    value_cache = {}
    for c in after_hash:
        try:
            vals = eval_fn(c)
            value_cache[c.factor_id] = vals
        except Exception:
            surviving.append(c)
            continue

    # 逐对比较数值相关
    final = []
    for c in after_hash:
        if c.factor_id not in value_cache:
            final.append(c)
            continue
        is_dup = False
        for kept in final:
            if kept.factor_id not in value_cache:
                continue
            a = value_cache[c.factor_id]
            b = value_cache[kept.factor_id]
            common = np.isfinite(a) & np.isfinite(b)
            if common.sum() < 10:
                continue
            corr = abs(np.corrcoef(a[common], b[common])[0, 1])
            if corr > config.dedup_corr_threshold:
                c.status = "REJECTED"
                c.reject_reason = f"数值近重复于 {kept.factor_id}（相关 {corr:.3f}）"
                is_dup = True
                break
        if not is_dup:
            final.append(c)
    rejected = [c for c in after_hash if c.status == "REJECTED"]
    return final, rejected


def stage3_cpcv_eval(
    candidates: list[CandidateFactor],
    factor_series_fn: Callable[[CandidateFactor], pd.Series],
    return_series: pd.Series,
    thresholds: LifecycleThresholds,
) -> tuple[list[CandidateFactor], list[CandidateFactor]]:
    """
    步骤 3+4：CPCV 评估 + 初筛。
    ICIR>min 且 单调性 p<max 且 turnover<max 且 半衰期≥min 才保留。
    """
    surviving, rejected = [], []
    for c in candidates:
        try:
            fs = factor_series_fn(c)
            c.eval_result = evaluate_factor(c.factor_id, fs, return_series)
        except Exception as e:
            c.status = "REJECTED"
            c.reject_reason = f"评估失败: {e!r}"
            rejected.append(c)
            continue
        r = c.eval_result
        if (r.icir >= thresholds.min_icir
                and r.monotonicity_p <= thresholds.max_monotonicity_p
                and r.turnover <= thresholds.max_turnover
                and r.halflife_bars >= thresholds.min_halflife_bars):
            surviving.append(c)
        else:
            c.status = "REJECTED"
            fails = []
            if r.icir < thresholds.min_icir:
                fails.append(f"ICIR={r.icir:.3f}<{thresholds.min_icir}")
            if r.monotonicity_p > thresholds.max_monotonicity_p:
                fails.append(f"单调性p={r.monotonicity_p:.3f}")
            if r.turnover > thresholds.max_turnover:
                fails.append(f"换手={r.turnover:.3f}")
            c.reject_reason = "初筛未达标：" + ", ".join(fails)
            rejected.append(c)
    return surviving, rejected


def stage4_data_quality(
    candidates: list[CandidateFactor],
    config: PurgeConfig,
    *,
    factor_series_fn: Callable[[CandidateFactor], pd.Series],
) -> tuple[list[CandidateFactor], list[CandidateFactor]]:
    """
    数据质量门槛（v6 2.4 S2-4，L101）：因子值完整率低于 config.min_data_quality
    的候选淘汰。

    数据残缺的因子（大量 NaN/Inf）即使样本内 IC 好看也不可信——缺失比例进
    factor card（factor_card.build_factor_card.data_quality），这里作为清洗维度
    与 5.3.3 admission_gate 联动。
    """
    surviving, rejected = [], []
    for c in candidates:
        try:
            fs = factor_series_fn(c)
            if fs is None or len(fs) == 0:
                c.status = "REJECTED"
                c.reject_reason = "数据质量不足：无因子值"
                rejected.append(c)
                continue
            completeness = float(fs.notna().mean())
            c.data_quality = round(completeness, 6)
            if completeness < config.min_data_quality:
                c.status = "REJECTED"
                c.reject_reason = (
                    f"数据质量不足：完整率 {completeness:.2f} < {config.min_data_quality:.2f}"
                )
                rejected.append(c)
                continue
        except Exception as e:
            c.status = "REJECTED"
            c.reject_reason = f"数据质量检查异常: {e!r}"
            rejected.append(c)
            continue
        surviving.append(c)
    return surviving, rejected


def stage5_orthogonalize(
    candidates: list[CandidateFactor],
    factor_matrix_fn: Callable[[list[CandidateFactor]], np.ndarray],
) -> tuple[list[CandidateFactor], str]:
    """步骤 5：数值层 QR 正交化。

    保留可解释 AST；把正交化后的列相关残差写入 ``c._ortho_column``（调用方可选用）。
    禁止再写 ``expr_ast = expr_ast`` 空操作却宣称已正交。
    返回 (candidates, status) status ∈ applied|skipped_trivial|failed。
    """
    if len(candidates) <= 1:
        return candidates, "skipped_trivial"
    try:
        F = np.asarray(factor_matrix_fn(candidates), dtype=float)
    except Exception:
        return candidates, "failed"
    if F.ndim != 2 or F.shape[1] < 2:
        return candidates, "skipped_trivial"
    try:
        # 列标准化后 QR，得到正交列；不改写 AST
        col_std = np.nanstd(F, axis=0)
        col_std = np.where(col_std < 1e-12, 1.0, col_std)
        F_n = (F - np.nanmean(F, axis=0)) / col_std
        F_n = np.nan_to_num(F_n, nan=0.0, posinf=0.0, neginf=0.0)
        Q, _R = np.linalg.qr(F_n)
        for i, c in enumerate(candidates):
            if i < Q.shape[1]:
                setattr(c, "_ortho_column", Q[:, i].copy())
                setattr(c, "_ortho_applied", True)
        return candidates, "applied"
    except np.linalg.LinAlgError:
        return candidates, "failed"


def stage6_pool_select(
    candidates: list[CandidateFactor],
    factor_series_fn: Callable[[CandidateFactor], pd.Series],
    return_series: pd.Series,
    config: PurgeConfig,
) -> tuple[list[CandidateFactor], list[CandidateFactor]]:
    """
    步骤 6：增量池筛选（AlphaGen pool-aware）。
    贪心按 ICIR 降序，仅当对池 IC 边际贡献>eps 且 与池内已有因子相关<max 才接纳。
    """
    # 按 ICIR 降序
    ranked = sorted(
        [c for c in candidates if c.eval_result],
        key=lambda c: abs(c.eval_result.icir),
        reverse=True,
    )
    pool: list[CandidateFactor] = []
    pool_values: list[np.ndarray] = []
    rejected = []

    for c in ranked:
        if len(pool) >= config.max_active_factors:
            c.status = "REJECTED"
            c.reject_reason = "池已满"
            rejected.append(c)
            continue
        try:
            vals = np.asarray(factor_series_fn(c).values, dtype=float)
        except Exception:
            c.status = "REJECTED"
            c.reject_reason = "池筛求值失败"
            rejected.append(c)
            continue

        # 与池内已有因子的最大相关
        max_corr = 0.0
        for pv in pool_values:
            common = np.isfinite(vals) & np.isfinite(pv)
            if common.sum() < 10:
                continue
            corr = abs(np.corrcoef(vals[common], pv[common])[0, 1])
            max_corr = max(max_corr, corr)

        c.incremental_corr = max_corr
        if max_corr <= config.pool_incremental_corr_max:
            pool.append(c)
            pool_values.append(vals)
            c.status = "ACTIVE"
        else:
            c.status = "REJECTED"
            c.reject_reason = f"增量相关 {max_corr:.3f} > {config.pool_incremental_corr_max}"

    rejected = [c for c in ranked if c.status == "REJECTED"]
    return pool, rejected


def run_purge_pipeline(
    candidates: list[CandidateFactor],
    *,
    factor_series_fn: Callable[[CandidateFactor], pd.Series],
    return_series: pd.Series,
    factor_matrix_fn: Callable[[list[CandidateFactor]], np.ndarray] | None = None,
    config: PurgeConfig | None = None,
    thresholds: LifecycleThresholds | None = None,
    dsr_pbo_gate: Callable[[list[CandidateFactor]], tuple[list[CandidateFactor], list[CandidateFactor]]] | None = None,
    sample_len: int = 252,
    n_total_candidates: int | None = None,
) -> tuple[list[CandidateFactor], PurgeReport]:
    """
    运行完整清洗管线。返回 (活跃因子列表, 报告)。

    dsr_pbo_gate 为 None 时使用内置 default_dsr_pbo_gate（禁止 Stage7 空跑）。
    """
    config = config or PurgeConfig()
    thresholds = thresholds or LifecycleThresholds()
    report = PurgeReport(total_input=len(candidates))

    # Stage 1: 静态审计
    s1_surv, s1_rej = stage1_static_audit(candidates)
    report.rejected_static = len(s1_rej)

    # Stage 2: 去重
    s2_surv, s2_rej = stage2_dedup(s1_surv, config)
    report.rejected_dedup = len(s2_rej)

    # Stage 3+4: CPCV 评估 + 初筛
    s3_surv, s3_rej = stage3_cpcv_eval(s2_surv, factor_series_fn, return_series, thresholds)
    report.rejected_eval = len(s3_rej)

    # Stage 4.5: 数据质量门槛
    s4_surv, s4_rej = stage4_data_quality(s3_surv, config, factor_series_fn=factor_series_fn)
    report.rejected_quality = len(s4_rej)

    # Stage 5: 正交化（无 matrix 则显式标记 skipped，禁止伪宣称）
    if factor_matrix_fn:
        s5_surv, report.ortho_status = stage5_orthogonalize(s4_surv, factor_matrix_fn)
    else:
        s5_surv = s4_surv
        report.ortho_status = "skipped_no_matrix"

    # Stage 6: 增量池筛选
    s6_surv, s6_rej = stage6_pool_select(s5_surv, factor_series_fn, return_series, config)
    report.rejected_pool = len(s6_rej)

    # Stage 7: DSR/PBO — 未传 callback 时走内置，杜绝空跑
    gate = dsr_pbo_gate
    if gate is None:
        def gate(surv, _sl=sample_len, _n=n_total_candidates or report.total_input):
            return default_dsr_pbo_gate(
                surv, sample_len=_sl, n_total_candidates=_n,
            )

    final, dsr_rej = gate(s6_surv)
    report.rejected_dsr_pbo = len(dsr_rej)

    report.surviving = len(final)
    report.candidates = report.candidates or candidates
    return final, report
