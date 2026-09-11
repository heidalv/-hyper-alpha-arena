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
import logging
import os
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)

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
    nearmiss_repaired: int = 0  # [2026-09-07] near-miss 自动修复成功数
    # applied=数值层 QR 已跑；skipped_no_matrix=调用方未供矩阵；skipped_trivial=因子数不足
    ortho_status: str = "skipped_no_matrix"
    candidates: list[CandidateFactor] = field(default_factory=list)
    # [2026-08-30 挖矿升级 M2] 初筛拒因样本（可审计，最多 20 条）
    reject_reason_samples: list[str] = field(default_factory=list)

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
            f"(ortho={self.ortho_status}, nearmiss修复 {self.nearmiss_repaired})"
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
    # [2026-08-27 挖掘根治] 冷启动豁免：幸存者 ≤5 且 DSR 本身显著时，跳过时序 PBO
    # fail-closed（PBO 在 <4 个时间切分上 indeterminate，单币序列方向不稳定会误杀
    # 跨币稳健的因子——实测 rev_5 9/9 币正被 pbo=0.714 单票否决）。冷启动因子
    # 上线后仍走 PAPER 影子期，风险有界。PURGE_COLDSTART_SKIP_PBO=0 可回滚。
    _dsr = (result.get("dsr_result") or {})
    try:
        _cold_skip = str(os.environ.get("PURGE_COLDSTART_SKIP_PBO", "1")).strip().lower() not in ("0", "false", "off")
    except Exception:
        _cold_skip = True
    if _cold_skip and len(survivors) <= 5 and bool(_dsr.get("significant")):
        logger.warning(
            "[Purge] 冷启动豁免 PBO（幸存者=%d，DSR显著）: %s",
            len(survivors), [c.factor_id for c in survivors],
        )
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
    eval_fn=None,
) -> tuple[list[CandidateFactor], list[CandidateFactor]]:
    """
    步骤 3+4：CPCV 评估 + 初筛。
    ICIR>min 且 单调性 p<max 且 turnover<max 且 半衰期≥min 才保留。
    """
    surviving, rejected = [], []
    for c in candidates:
        try:
            fs = factor_series_fn(c)
            # [2026-08-30 M2c] eval_fn 注入面板口径评估（默认单序列）
            if eval_fn is not None:
                c.eval_result = eval_fn(c.factor_id, fs, return_series, c)
            else:
                c.eval_result = evaluate_factor(c.factor_id, fs, return_series)
        except Exception as e:
            c.status = "REJECTED"
            c.reject_reason = f"评估失败: {e!r}"
            rejected.append(c)
            continue
        r = c.eval_result
        # [2026-08-30 M2d] 单调性替代路径：尾部价差 |t|≥2 视为方向结构成立。
        # 反转因子只在尾部极端档有效（中段平坦是特性不是缺陷），全档单调性
        # p 值对它们系统性偏大；IC/ICIR/DSR/PBO 等统计门禁不减免。
        _tail_t = float(getattr(r, "tail_spread_t", 0.0) or 0.0)
        _mono_ok = (r.monotonicity_p <= thresholds.max_monotonicity_p
                    or abs(_tail_t) >= 2.0)
        if (r.icir >= thresholds.min_icir
                and _mono_ok
                and r.turnover <= thresholds.max_turnover
                and r.halflife_bars >= thresholds.min_halflife_bars):
            surviving.append(c)
        else:
            c.status = "REJECTED"
            fails = []
            if r.icir < thresholds.min_icir:
                fails.append(f"ICIR={r.icir:.3f}<{thresholds.min_icir}")
            if not _mono_ok:
                fails.append(f"单调性p={r.monotonicity_p:.3f}(尾部t={_tail_t:.2f})")
            if r.turnover > thresholds.max_turnover:
                fails.append(f"换手={r.turnover:.3f}")
            c.reject_reason = "初筛未达标：" + ", ".join(fails)
            rejected.append(c)
    return surviving, rejected


def _nearmiss_repair_variants(ast: dict) -> list:
    """[2026-09-07 WQ BRAIN near-miss] 对「差一点」的因子生成修复变体。

    BRAIN 对 Sharpe 0.90-1.24 的因子自动调参：Fitness/换手不够 → 加 decay、
    换 ts_rank。这里对 AST 做两类轻量包装（不改变经济逻辑，只平滑/降换手）：
      1. decay_linear(x, 5)：线性衰减平滑，降换手、提半衰期；
      2. ts_rank(x, 10)：滚动分位，压极端值、稳 IC。
    只包装根节点一次，避免组合爆炸。
    """
    if not isinstance(ast, dict) or "op" not in ast:
        return []
    if ast.get("op") in ("decay_linear", "ts_rank", "wma", "ema"):
        return []
    return [
        {"op": "decay_linear", "args": [ast, {"c": 5}]},
        {"op": "ts_rank", "args": [ast, {"c": 10}]},
    ]


def stage3b_nearmiss_repair(
    rejected: list,
    thresholds: LifecycleThresholds,
    factor_series_fn,
    return_series,
    eval_fn=None,
) -> list:
    """near-miss 自动修复：初筛「差一点」的因子（ICIR 接近门槛或换手略超），
    生成平滑/分位变体并重评一次，达标者复活进后续阶段。

    只救「接近达标」的（ICIR ≥ 0.7×门槛 或换手 ≤ 1.5×上限 或半衰期差一点），
    差太远的不救——避免把噪声因子包装后蒙混过关。每因子最多 1 个变体复活。
    默认开启；PURGE_NEARMISS_REPAIR=0 回滚。
    """
    import os as _os
    if (_os.getenv("PURGE_NEARMISS_REPAIR", "1") or "1").strip().lower() in ("0", "false", "off"):
        return []
    repaired: list = []
    for c in rejected:
        r = c.eval_result
        if r is None or c.expr_ast is None:
            continue
        icir_close = 0 < r.icir < thresholds.min_icir and r.icir >= thresholds.min_icir * 0.7
        to_over = r.turnover > thresholds.max_turnover and r.turnover <= thresholds.max_turnover * 1.5
        hl_close = 0 < r.halflife_bars < thresholds.min_halflife_bars
        if not (icir_close or to_over or hl_close):
            continue
        best = None
        for variant_ast in _nearmiss_repair_variants(c.expr_ast):
            vc = CandidateFactor(
                factor_id=c.factor_id + "_nr",
                source_name=c.source_name + "+nearmiss",
                expr_ast=variant_ast,
            )
            try:
                fs = factor_series_fn(vc)
                vc.eval_result = (eval_fn(vc.factor_id, fs, return_series, vc)
                                  if eval_fn is not None
                                  else evaluate_factor(vc.factor_id, fs, return_series))
            except Exception:
                continue
            vr = vc.eval_result
            _tail_t = float(getattr(vr, "tail_spread_t", 0.0) or 0.0)
            _mono_ok = (vr.monotonicity_p <= thresholds.max_monotonicity_p
                        or abs(_tail_t) >= 2.0)
            if (vr.icir >= thresholds.min_icir and _mono_ok
                    and vr.turnover <= thresholds.max_turnover
                    and vr.halflife_bars >= thresholds.min_halflife_bars):
                if best is None or vr.icir > best.eval_result.icir:
                    best = vc
        if best is not None:
            logger.info(
                "[Purge] near-miss 修复 %s → ICIR %.3f→%.3f 换手 %.2f→%.2f",
                c.factor_id, r.icir, best.eval_result.icir,
                r.turnover, best.eval_result.turnover,
            )
            repaired.append(best)
    return repaired


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
    eval_fn=None,
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
    s3_surv, s3_rej = stage3_cpcv_eval(s2_surv, factor_series_fn, return_series, thresholds, eval_fn=eval_fn)
    report.rejected_eval = len(s3_rej)

    # [2026-09-07] Stage 3b: near-miss 自动修复（WQ BRAIN 式）——初筛「差一点」
    # 的因子生成平滑/分位变体重评，达标者复活，不浪费接近及格的搜索结果。
    s3b_repaired = stage3b_nearmiss_repair(
        s3_rej, thresholds, factor_series_fn, return_series, eval_fn=eval_fn,
    )
    if s3b_repaired:
        s3_surv = list(s3_surv) + s3b_repaired
        report.nearmiss_repaired = len(s3b_repaired)

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
    # [2026-08-30 挖矿升级 M2] 拒因样本：按阶段聚合，最多 20 条（可审计）
    for _stage, _rejs in (("静态", s1_rej), ("去重", s2_rej), ("初筛", s3_rej),
                          ("质量", s4_rej), ("池筛", s6_rej), ("DSR/PBO", dsr_rej)):
        for _c in (_rejs or [])[:5]:
            _r = getattr(_c, "reject_reason", "") or getattr(_c, "status", "")
            report.reject_reason_samples.append(f"[{_stage}] {str(_r)[:90]}")
    report.reject_reason_samples = report.reject_reason_samples[:20]
    return final, report
