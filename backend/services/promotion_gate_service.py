"""
shadow → canary → full 自动晋升门（整改 6.2.6 / §9.3）。

基于统计检验（DSR / 胜率 / 回撤 / 最小样本）评估候选是否可从
shadow（镜像不影响实盘）→ canary（小资本分配）→ full（全量）。

与 RuntimeGovernor 集成：达标候选提交 intent，由多源仲裁后生效。
零风险：PROMOTION_GATE_ENABLED=false 时恒返回 hold。
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DECISIONS_LOG = os.path.join("data", "promotion_gate_decisions.jsonl")


class PromotionStage(str, Enum):
    SHADOW = "shadow"
    CANARY = "canary"
    FULL = "full"


@dataclass
class PromotionMetrics:
    candidate_id: str
    domain: str = "factor_weighting"
    stage: PromotionStage = PromotionStage.SHADOW
    sharpe: float = 0.0
    win_rate: float = 0.0
    max_drawdown: float = 0.0
    trade_count: int = 0
    n_trials: int = 1
    returns: Optional[List[float]] = None
    canary_pnl_delta: float = 0.0
    control_pnl_delta: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PromotionDecision:
    candidate_id: str
    from_stage: PromotionStage
    to_stage: PromotionStage
    approved: bool
    reason: str
    dsr: Optional[float] = None
    pbo: Optional[float] = None
    evaluated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "from_stage": self.from_stage.value,
            "to_stage": self.to_stage.value,
            "approved": self.approved,
            "reason": self.reason,
            "dsr": self.dsr,
            "pbo": self.pbo,
            "evaluated_at": self.evaluated_at,
        }


def is_enabled() -> bool:
    return os.environ.get("PROMOTION_GATE_ENABLED", "true").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _next_stage(current: PromotionStage) -> PromotionStage:
    if current == PromotionStage.SHADOW:
        return PromotionStage.CANARY
    if current == PromotionStage.CANARY:
        return PromotionStage.FULL
    return PromotionStage.FULL


def _compute_dsr(metrics: PromotionMetrics) -> Optional[float]:
    try:
        from backend.services.backtest_engine.overfitting_metrics import deflated_sharpe_ratio
        import numpy as np

        rets = metrics.returns
        if not rets or len(rets) < 5:
            return None
        arr = np.asarray(rets, dtype=float)
        sr = float(arr.mean() / (arr.std() + 1e-12) * (len(arr) ** 0.5))
        dsr, _ = deflated_sharpe_ratio(sr, max(metrics.n_trials, 1), arr)
        return float(dsr)
    except Exception as exc:
        logger.debug("[PromotionGate] DSR 计算跳过: %s", exc)
        return None


def _log_decision(decision: PromotionDecision) -> None:
    try:
        os.makedirs(os.path.dirname(DECISIONS_LOG) or "data", exist_ok=True)
        with open(DECISIONS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(decision.to_dict(), ensure_ascii=False) + "\n")
    except Exception:
        pass


def evaluate_promotion(metrics: PromotionMetrics) -> PromotionDecision:
    """评估单候选是否可晋升到下一阶段。"""
    nxt = _next_stage(metrics.stage)
    if not is_enabled():
        return PromotionDecision(
            metrics.candidate_id, metrics.stage, nxt, False, "PROMOTION_GATE_ENABLED=false",
        )

    min_trades = _env_int("PROMOTION_MIN_TRADES", 20)
    min_wr = _env_float("PROMOTION_MIN_WIN_RATE", 0.48)
    max_dd = _env_float("PROMOTION_MAX_DRAWDOWN", 0.15)
    min_dsr = _env_float("PROMOTION_MIN_DSR", 0.55)
    canary_min_delta = _env_float("PROMOTION_CANARY_MIN_PNL_DELTA", 0.005)

    if metrics.trade_count < min_trades:
        return PromotionDecision(
            metrics.candidate_id, metrics.stage, nxt, False,
            f"样本不足 {metrics.trade_count}<{min_trades}",
        )
    if metrics.win_rate < min_wr:
        return PromotionDecision(
            metrics.candidate_id, metrics.stage, nxt, False,
            f"胜率 {metrics.win_rate:.3f}<{min_wr}",
        )
    if metrics.max_drawdown > max_dd:
        return PromotionDecision(
            metrics.candidate_id, metrics.stage, nxt, False,
            f"回撤 {metrics.max_drawdown:.3f}>{max_dd}",
        )

    dsr = _compute_dsr(metrics)
    if dsr is not None and dsr < min_dsr:
        return PromotionDecision(
            metrics.candidate_id, metrics.stage, nxt, False,
            f"DSR {dsr:.3f}<{min_dsr}", dsr=dsr,
        )

    if metrics.stage == PromotionStage.SHADOW and nxt == PromotionStage.CANARY:
        pass  # shadow→canary：统计门槛已够
    elif metrics.stage == PromotionStage.CANARY and nxt == PromotionStage.FULL:
        delta = metrics.canary_pnl_delta - metrics.control_pnl_delta
        if delta < canary_min_delta:
            return PromotionDecision(
                metrics.candidate_id, metrics.stage, nxt, False,
                f"canary A/B delta {delta:.4f}<{canary_min_delta}",
                dsr=dsr,
            )

    decision = PromotionDecision(
        metrics.candidate_id, metrics.stage, nxt, True,
        f"统计检验通过 → {nxt.value}", dsr=dsr,
    )
    _log_decision(decision)
    return decision


def submit_to_runtime_governor(
    decision: PromotionDecision,
    *,
    patch_keys: Optional[Dict[str, Any]] = None,
) -> bool:
    """将晋升决策写入 RuntimeGovernor 待审批队列（Paper 可自动批）。"""
    if not decision.approved:
        return False
    try:
        import uuid
        from backend.services.runtime_governor import PENDING_DIR, TuningPatch, runtime_governor

        keys = dict(patch_keys or {})
        keys.update({
            "_patch_type": "promotion_gate",
            "candidate_id": decision.candidate_id,
            "from_stage": decision.from_stage.value,
            "to_stage": decision.to_stage.value,
            "dsr": decision.dsr,
            "domain": (patch_keys or {}).get("domain", "factor_weighting"),
        })
        patch = TuningPatch(
            patch_id=str(uuid.uuid4())[:12],
            keys=keys,
            reason=decision.reason,
        )
        os.makedirs(PENDING_DIR, exist_ok=True)
        path = os.path.join(PENDING_DIR, f"{patch.patch_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(patch.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(
            "[PromotionGate] 晋升待审批: %s %s→%s patch=%s",
            decision.candidate_id, decision.from_stage.value, decision.to_stage.value, patch.patch_id,
        )
        return True
    except Exception as exc:
        logger.warning("[PromotionGate] RuntimeGovernor 提交失败: %s", exc)
        return False


def scan_and_promote(candidates: List[PromotionMetrics]) -> List[PromotionDecision]:
    """批量扫描候选并尝试晋升。"""
    out: List[PromotionDecision] = []
    for m in candidates:
        d = evaluate_promotion(m)
        if d.approved:
            submit_to_runtime_governor(d, patch_keys={"domain": m.domain})
        out.append(d)
    return out
