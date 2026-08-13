"""
因子生命周期状态机（P1.3，方案 §2.2）。

核心纪律（R4）：因子状态转换由客观指标阈值自动驱动，不依赖人工"看着差不多就过"。
仅高破坏性转换（→SMALL_LIVE / →ACTIVE）保留 OversightAgent 审批；审批超时默认拒。

状态流：
    DRAFT ──(audit pass)──► CANDIDATE ──(CPCV IC/单调性/turnover)──► ORTHO
    ORTHO ──(增量相关/DSR/PBO/capacity)──► PAPER ──[审批/超时拒]──► SMALL_LIVE
    SMALL_LIVE ──[审批/超时拒]──► ACTIVE ──(退化)──► DEWEIGHT ──► QUARANTINE
    任何 ──(look-ahead/bug)──► REJECTED

转换由 LifecycleEngine.evaluate(factor_state, metrics) 判定，返回 TransitionDecision。
ShadowJudgeAgent（P4.5）周期性调用本引擎驱动实际转换。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml


class FactorState(str, Enum):
    DRAFT = "DRAFT"
    CANDIDATE = "CANDIDATE"
    ORTHO = "ORTHO"
    PAPER = "PAPER"           # 纸上影子
    SMALL_LIVE = "SMALL_LIVE" # 小仓影子实盘
    ACTIVE = "ACTIVE"         # 全仓
    DEWEIGHT = "DEWEIGHT"     # 降权
    QUARANTINE = "QUARANTINE" # 隔离
    REJECTED = "REJECTED"


# 需要审批的高破坏性转换目标状态
APPROVAL_REQUIRED: frozenset[FactorState] = frozenset({FactorState.SMALL_LIVE, FactorState.ACTIVE})

# 审批超时（秒）—— 超时默认拒（不留悬置，方案 R4）
APPROVAL_TIMEOUT_SEC = 24 * 3600


@dataclass(frozen=True)
class LifecycleThresholds:
    """状态转换阈值（方案：阈值本身也走 DSR 校验防手调过拟合）。"""
    # CANDIDATE → ORTHO（CPCV 评估）
    min_icir: float = 0.40   # [2026-08-13 P2-11] 0.30→0.40 收紧（诊断：准入门槛系统性偏松）
    max_monotonicity_p: float = 0.05   # 单调性 p 值（越小越单调）
    max_turnover: float = 0.70
    min_halflife_bars: int = 5
    # ORTHO → PAPER
    max_incremental_corr: float = 0.50  # 对活跃池的增量相关
    min_dsr_significant: bool = True    # Deflated Sharpe 显著
    max_pbo: float = 0.35               # [2026-08-13 P2-11] 0.50→0.35（诊断：PBO 门槛低于行业常规）
    min_capacity_usd: float = 1e5       # 容量下限（P1.6）
    # PAPER → SMALL_LIVE
    min_paper_sharpe: float = 1.0
    max_live_deviation: float = 0.003   # 实盘对齐偏差 0.3%
    paper_min_days: int = 10   # [2026-08-13 P2-11] 5→10（影子期过短不足以暴露 regime 切换）
    # SMALL_LIVE → ACTIVE
    small_live_min_days: int = 14
    # ACTIVE → DEWEIGHT（退化触发，任一满足）
    decay_icir_threshold: float = 0.15
    decay_consecutive_days: int = 3
    decay_halflife_min_bars: int = 3
    decay_capacity_min_usd: float = 5e4


@dataclass
class FactorMetrics:
    """单个因子当前评估指标（由 CPCV/IC 评估器/容量模型/Paper 影子产出）。"""
    factor_id: str
    state: FactorState
    icir: float = 0.0
    monotonicity_p: float = 1.0
    turnover: float = 1.0
    halflife_bars: int = 0
    incremental_corr: float = 1.0
    dsr_significant: bool = False
    pbo: float = 1.0
    capacity_usd: float = 0.0
    paper_sharpe: float = 0.0
    live_deviation: float = 1.0
    paper_days: int = 0
    small_live_days: int = 0
    decay_consecutive_days: int = 0
    audit_passed: bool = False       # 表达式合法性 + 无 look-ahead
    has_bug: bool = False            # 发现 look-ahead / 计算错误


@dataclass
class TransitionDecision:
    """状态机评估结果。"""
    factor_id: str
    from_state: FactorState
    to_state: FactorState
    auto: bool                       # 是否自动转换（True=阈值达标自动；False=需审批）
    reason: str
    conditions_met: dict = field(default_factory=dict)
    conditions_failed: dict = field(default_factory=dict)


def _check_cand_to_ortho(m: FactorMetrics, t: LifecycleThresholds) -> tuple[dict, dict]:
    met, fail = {}, {}
    (met if m.icir >= t.min_icir else fail)["icir"] = m.icir
    (met if m.monotonicity_p <= t.max_monotonicity_p else fail)["monotonicity_p"] = m.monotonicity_p
    (met if m.turnover <= t.max_turnover else fail)["turnover"] = m.turnover
    (met if m.halflife_bars >= t.min_halflife_bars else fail)["halflife_bars"] = m.halflife_bars
    return met, fail


def _check_ortho_to_paper(m: FactorMetrics, t: LifecycleThresholds) -> tuple[dict, dict]:
    met, fail = {}, {}
    (met if m.incremental_corr <= t.max_incremental_corr else fail)["incremental_corr"] = m.incremental_corr
    (met if m.dsr_significant == t.min_dsr_significant else fail)["dsr_significant"] = m.dsr_significant
    (met if m.pbo <= t.max_pbo else fail)["pbo"] = m.pbo
    (met if m.capacity_usd >= t.min_capacity_usd else fail)["capacity_usd"] = m.capacity_usd
    return met, fail


def _check_paper_to_smalllive(m: FactorMetrics, t: LifecycleThresholds) -> tuple[dict, dict]:
    met, fail = {}, {}
    (met if m.paper_sharpe >= t.min_paper_sharpe else fail)["paper_sharpe"] = m.paper_sharpe
    (met if m.live_deviation <= t.max_live_deviation else fail)["live_deviation"] = m.live_deviation
    (met if m.paper_days >= t.paper_min_days else fail)["paper_days"] = m.paper_days
    return met, fail


def _check_smalllive_to_active(m: FactorMetrics, t: LifecycleThresholds) -> tuple[dict, dict]:
    met, fail = {}, {}
    (met if m.small_live_days >= t.small_live_min_days else fail)["small_live_days"] = m.small_live_days
    (met if m.icir >= t.min_icir else fail)["icir"] = m.icir  # 未退化
    return met, fail


def _check_active_decay(m: FactorMetrics, t: LifecycleThresholds) -> bool:
    """退化触发（任一满足即降权）。"""
    if m.icir < t.decay_icir_threshold and m.decay_consecutive_days >= t.decay_consecutive_days:
        return True
    if m.halflife_bars < t.decay_halflife_min_bars:
        return True
    if m.capacity_usd < t.decay_capacity_min_usd:
        return True
    return False


def evaluate_transition(
    metrics: FactorMetrics,
    thresholds: LifecycleThresholds | None = None,
) -> TransitionDecision:
    """
    评估因子状态转换。返回 TransitionDecision。

    调用方（ShadowJudgeAgent）据此决定：
        - auto=True 且 to_state 不需审批 → 直接转换
        - auto=True 且 to_state 需审批 → 发审批，超时默认拒
        - auto=False → 留在原状态
    """
    t = thresholds or LifecycleThresholds()
    s = metrics.state
    fid = metrics.factor_id

    # 紧急拒绝：发现 bug / look-ahead
    if metrics.has_bug:
        return TransitionDecision(fid, s, FactorState.REJECTED, auto=True,
                                  reason="发现 look-ahead/计算错误 → REJECTED")

    # 各状态转换
    if s == FactorState.DRAFT:
        if metrics.audit_passed:
            return TransitionDecision(fid, s, FactorState.CANDIDATE, auto=True,
                                      reason="表达式审计通过 → CANDIDATE")
        return TransitionDecision(fid, s, s, auto=False, reason="审计未通过")

    if s == FactorState.CANDIDATE:
        met, fail = _check_cand_to_ortho(metrics, t)
        if not fail:
            return TransitionDecision(fid, s, FactorState.ORTHO, auto=True,
                                      reason="CPCV 指标达标 → ORTHO", conditions_met=met)
        return TransitionDecision(fid, s, s, auto=False, reason="CPCV 未达标",
                                  conditions_failed=fail)

    if s == FactorState.ORTHO:
        met, fail = _check_ortho_to_paper(metrics, t)
        if not fail:
            # PAPER 需审批（高破坏性，进入影子）
            return TransitionDecision(fid, s, FactorState.PAPER, auto=True,
                                      reason="增量相关/DSR/PBO/capacity 达标 → PAPER（需审批）",
                                      conditions_met=met)
        return TransitionDecision(fid, s, s, auto=False, reason="池筛选未达标",
                                  conditions_failed=fail)

    if s == FactorState.PAPER:
        met, fail = _check_paper_to_smalllive(metrics, t)
        if not fail:
            # SMALL_LIVE 需审批
            return TransitionDecision(fid, s, FactorState.SMALL_LIVE, auto=True,
                                      reason="纸上达标 → SMALL_LIVE（需审批）",
                                      conditions_met=met)
        return TransitionDecision(fid, s, s, auto=False, reason="纸上未达标",
                                  conditions_failed=fail)

    if s == FactorState.SMALL_LIVE:
        met, fail = _check_smalllive_to_active(metrics, t)
        if not fail:
            # ACTIVE 需审批
            return TransitionDecision(fid, s, FactorState.ACTIVE, auto=True,
                                      reason="小仓达标 → ACTIVE（需审批）",
                                      conditions_met=met)
        if _check_active_decay(metrics, t):
            return TransitionDecision(fid, s, FactorState.DEWEIGHT, auto=True,
                                      reason="小仓期退化 → DEWEIGHT")
        return TransitionDecision(fid, s, s, auto=False, reason="小仓期未达标",
                                  conditions_failed=fail)

    if s == FactorState.ACTIVE:
        if _check_active_decay(metrics, t):
            return TransitionDecision(fid, s, FactorState.DEWEIGHT, auto=True,
                                      reason="退化触发 → DEWEIGHT")
        return TransitionDecision(fid, s, s, auto=False, reason="正常运行")

    if s == FactorState.DEWEIGHT:
        if _check_active_decay(metrics, t):
            return TransitionDecision(fid, s, FactorState.QUARANTINE, auto=True,
                                      reason="持续退化 → QUARANTINE")
        return TransitionDecision(fid, s, FactorState.ACTIVE, auto=True,
                                  reason="退化恢复 → ACTIVE")

    # QUARANTINE / REJECTED / 终态
    return TransitionDecision(fid, s, s, auto=False, reason="终态")


def needs_approval(decision: TransitionDecision) -> bool:
    """该转换是否需要 OversightAgent 审批。"""
    return decision.to_state in APPROVAL_REQUIRED and decision.to_state != decision.from_state


# ============ 阈值配置加载（config/factor_lifecycle.yaml） ============

def load_thresholds(yaml_path: str | Path | None = None) -> LifecycleThresholds:
    """从 YAML 加载阈值（缺失则用默认）。方案要求阈值本身受 DSR 校验。"""
    if yaml_path is None:
        yaml_path = Path(__file__).resolve().parents[2] / "config" / "factor_lifecycle.yaml"
    yaml_path = Path(yaml_path)
    if not yaml_path.exists():
        return LifecycleThresholds()
    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        kwargs = {k: v for k, v in data.items() if k in LifecycleThresholds.__dataclass_fields__}
        return LifecycleThresholds(**kwargs)
    except Exception:
        return LifecycleThresholds()
