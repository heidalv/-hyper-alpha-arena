"""
晋升门扫描编排 — 从 ML/因子/QAA 灰度收集候选，驱动 shadow→canary→full。

由 learning_loop 维护周期异步触发（G4 离峰）；决策写入 promotion_gate_service，
Paper 模式可经 RuntimeGovernor 自动批准并落盘 registry。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

REGISTRY_PATH = os.path.join("data", "promotion_gate_registry.json")
_STATS: Dict[str, Any] = {
    "last_scan_ts": 0.0,
    "last_candidates": 0,
    "last_promoted": 0,
    "last_error": "",
    "in_flight": False,
}
_LOCK = threading.Lock()
_LAST_SCAN_MONO = 0.0

# 各阶段 hybrid learned 融合权重（#4 渐进迁移）
_STAGE_BLEND = {
    "shadow": 0.0,    # 仅记录 shadow 指标，不混入实盘信号
    "canary": 0.22,   # 小比例 canary 资本
    "full": 0.45,     # 全量融合（等同 HYBRID_LEARNED_BLEND 默认）
}


def _load_registry() -> Dict[str, Any]:
    if not os.path.isfile(REGISTRY_PATH):
        return {"candidates": {}}
    try:
        with open(REGISTRY_PATH, encoding="utf-8") as f:
            data = json.load(f) or {}
        if "candidates" not in data:
            data["candidates"] = {}
        return data
    except Exception:
        return {"candidates": {}}


def _save_registry(data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(REGISTRY_PATH) or "data", exist_ok=True)
    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_scan_stats() -> Dict[str, Any]:
    with _LOCK:
        out = dict(_STATS)
    out["registry"] = _load_registry().get("candidates", {})
    return out


def get_candidate_stage(candidate_id: str) -> str:
    reg = _load_registry().get("candidates", {})
    entry = reg.get(candidate_id) or {}
    return str(entry.get("stage") or "shadow")


def get_effective_learned_blend(candidate_id: str = "ml_learned_weighting") -> float:
    """供 factor_evaluation_pipeline hybrid 融合读取的阶段权重。"""
    stage = get_candidate_stage(candidate_id)
    env_override = os.environ.get("HYBRID_LEARNED_BLEND")
    if stage == "full" and env_override:
        try:
            return max(0.0, min(1.0, float(env_override)))
        except ValueError:
            pass
    return float(_STAGE_BLEND.get(stage, 0.0))


def apply_promotion_stage(
    candidate_id: str,
    to_stage: str,
    *,
    domain: str = "factor_weighting",
    dsr: Optional[float] = None,
    patch_id: Optional[str] = None,
) -> bool:
    """RuntimeGovernor approve 或扫描通过后落盘阶段。"""
    data = _load_registry()
    cands = data.setdefault("candidates", {})
    prev = (cands.get(candidate_id) or {}).get("stage", "shadow")
    cands[candidate_id] = {
        "stage": to_stage,
        "domain": domain,
        "previous_stage": prev,
        "dsr": dsr,
        "patch_id": patch_id,
        "updated_at": time.time(),
        "blend_alpha": _STAGE_BLEND.get(to_stage, 0.0),
    }
    _save_registry(data)

    # [2026-07-11 修复] 原逻辑在候选晋升 full 时，把 hybrid 融合权重比例
    # (_STAGE_BLEND["full"]=0.45，量级 0.35~0.55) 误当成 maturity_global_n1
    # (语义是"全局维度 warmup→growth 分界样本数"，schema 定义 min=5/max=60 的整数)
    # 提交给了 RuntimeGovernor。类型/量级完全不匹配——0.45 会被 apply_patches 按
    # schema 下限强行 clamp 成 5，把全局 warmup 样本门槛砍到近乎失效，且与
    # "融合权重提升"这个理由毫无逻辑关联，纯属 key 写错的历史 bug（复核历史意图时
    # 发现 data/runtime_tuning_intents.json 里有一条来源正是这里，已一并撤销）。
    # 这里直接移除这条误提交，晋升阶段的记录已经通过上面的 registry 落盘完成，
    # 不需要也不应该联动去改 maturity 相关调参。
    if candidate_id.startswith("qaa_") and to_stage in ("canary", "full"):
        _sync_qaa_grayscale(candidate_id, to_stage)

    logger.info("[PromotionScan] 阶段更新 %s: %s → %s", candidate_id, prev, to_stage)
    return True


def _sync_qaa_grayscale(candidate_id: str, stage: str) -> None:
    """QAA 灰度计划与晋升门阶段对齐（canary 观察 / full 确认）。"""
    strategy_id = candidate_id.replace("qaa_", "", 1)
    if not strategy_id:
        return
    try:
        from backend.services.qaa_evolution_bridge import qaa_bridge
        plan = qaa_bridge._grayscale_plans.get(strategy_id)
        if plan is None:
            return
        if stage == "full" and plan.status == "observing":
            from backend.database.connection import SessionLocal
            db = SessionLocal()
            try:
                qaa_bridge._confirm_grayscale(plan, db, 0.0, 0.0)
            finally:
                db.close()
    except Exception as exc:
        logger.debug("[PromotionScan] QAA 灰度同步跳过: %s", exc)


def _trade_metrics_from_paper(db, *, days: int = 14) -> Tuple[int, float, float, List[float]]:
    """从 PaperOrder 近期平仓估算晋升指标。"""
    from backend.database.models import PaperOrder

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    rows = (
        db.query(PaperOrder)
        .filter(
            PaperOrder.status == "filled",
            PaperOrder.pnl.isnot(None),
            PaperOrder.created_at >= cutoff,
        )
        .order_by(PaperOrder.created_at.asc())
        .limit(500)
        .all()
    )
    if not rows:
        return 0, 0.0, 0.0, []

    pnls = [float(r.pnl or 0) for r in rows]
    wins = sum(1 for p in pnls if p > 0)
    wr = wins / len(pnls)
    equity = 500000.0
    peak = equity
    dd = 0.0
    cum = equity
    rets: List[float] = []
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        dd = max(dd, (peak - cum) / peak if peak > 0 else 0)
        rets.append(p / equity)
    sharpe = 0.0
    if len(rets) >= 5:
        import numpy as np
        arr = np.asarray(rets, dtype=float)
        sharpe = float(arr.mean() / (arr.std() + 1e-12) * (len(arr) ** 0.5))
    return len(pnls), wr, dd, rets


def _strategy_metrics(db, strategy_id: str) -> Tuple[int, float, float, float, List[float]]:
    from backend.database.models import StrategyMemory, StrategyTrade

    mem = db.query(StrategyMemory).filter(StrategyMemory.strategy_id == strategy_id).first()
    trades = (
        db.query(StrategyTrade)
        .filter(StrategyTrade.strategy_id == strategy_id, StrategyTrade.pnl_pct.isnot(None))
        .order_by(StrategyTrade.exit_time.desc())
        .limit(120)
        .all()
    )
    rets = [float(t.pnl_pct or 0) for t in reversed(trades)]
    if mem:
        return (
            int(mem.total_trades or len(rets)),
            float(mem.win_rate or 0),
            float(mem.max_drawdown or 0),
            float(mem.sharpe_ratio or 0),
            rets,
        )
    if not rets:
        return 0, 0.0, 0.0, 0.0, []
    wins = sum(1 for r in rets if r > 0)
    return len(rets), wins / len(rets), 0.1, 0.0, rets


def collect_candidates(db) -> List[Any]:
    """收集可参与晋升扫描的候选（PromotionMetrics）。"""
    from backend.services.promotion_gate_service import PromotionMetrics, PromotionStage

    out: List[PromotionMetrics] = []
    reg = _load_registry().get("candidates", {})

    def _stage_enum(cid: str) -> PromotionStage:
        s = str((reg.get(cid) or {}).get("stage") or "shadow")
        try:
            return PromotionStage(s)
        except ValueError:
            return PromotionStage.SHADOW

    # 1) ML learned 因子层（全局）
    try:
        from backend.services.ml.activation_service import get_learned_weighting_singleton, is_ml_activation_enabled

        learned = get_learned_weighting_singleton() if is_ml_activation_enabled() else None
        if learned is not None and getattr(learned, "model", None) is not None:
            n, wr, dd, rets = _trade_metrics_from_paper(db)
            cid = "ml_learned_weighting"
            out.append(PromotionMetrics(
                candidate_id=cid,
                domain="factor_weighting",
                stage=_stage_enum(cid),
                sharpe=0.0,
                win_rate=wr,
                max_drawdown=dd,
                trade_count=n,
                n_trials=max(1, int(os.environ.get("ML_LIVE_RETRAIN_HOURS", "12"))),
                returns=rets if len(rets) >= 5 else None,
            ))
    except Exception as exc:
        logger.debug("[PromotionScan] learned 候选跳过: %s", exc)

    # 2) hybrid 模式整体候选
    mode = os.environ.get("FACTOR_WEIGHTING_MODE", "regime").strip().lower()
    if mode in ("hybrid", "learned"):
        cid = "factor_hybrid_mode"
        n, wr, dd, rets = _trade_metrics_from_paper(db)
        out.append(PromotionMetrics(
            candidate_id=cid,
            domain="factor_weighting",
            stage=_stage_enum(cid),
            win_rate=wr,
            max_drawdown=dd,
            trade_count=n,
            returns=rets if len(rets) >= 5 else None,
        ))

    # 3) QAA 灰度计划 → canary 候选
    try:
        from backend.services.qaa_evolution_bridge import qaa_bridge

        for sid, plan in list(getattr(qaa_bridge, "_grayscale_plans", {}).items()):
            if plan.status != "observing":
                continue
            cid = f"qaa_{sid}"
            from backend.database.models import StrategyTrade

            cutoff = plan.observation_started_at or (time.time() - 86400)
            canary_trades = db.query(StrategyTrade).filter(
                StrategyTrade.strategy_id == sid,
                StrategyTrade.symbol.in_(
                    [s.upper() for s in plan.canary_symbols]
                    + [s.lower() for s in plan.canary_symbols]
                ),
                StrategyTrade.pnl_pct.isnot(None),
            ).all()
            control_trades = db.query(StrategyTrade).filter(
                StrategyTrade.strategy_id == sid,
                StrategyTrade.symbol.in_(
                    [s.upper() for s in plan.control_symbols]
                    + [s.lower() for s in plan.control_symbols]
                ),
                StrategyTrade.pnl_pct.isnot(None),
            ).all()
            canary_avg = (
                sum(float(t.pnl_pct or 0) for t in canary_trades) / len(canary_trades)
                if canary_trades else 0.0
            )
            control_avg = (
                sum(float(t.pnl_pct or 0) for t in control_trades) / len(control_trades)
                if control_trades else 0.0
            )
            n, wr, dd, sharpe, rets = _strategy_metrics(db, sid)
            stage = _stage_enum(cid)
            if stage == PromotionStage.SHADOW and plan.observation_started_at:
                stage = PromotionStage.CANARY
            out.append(PromotionMetrics(
                candidate_id=cid,
                domain="qaa_evolution",
                stage=stage,
                sharpe=sharpe,
                win_rate=wr,
                max_drawdown=dd,
                trade_count=n,
                returns=rets if len(rets) >= 5 else None,
                canary_pnl_delta=canary_avg,
                control_pnl_delta=control_avg,
                extra={"plan_id": plan.plan_id},
            ))
    except Exception as exc:
        logger.debug("[PromotionScan] QAA 候选跳过: %s", exc)

    # 4) 演化策略（StrategyMemory 有样本的 active 策略）
    try:
        from backend.database.models import AIStrategy, StrategyMemory

        rows = (
            db.query(AIStrategy, StrategyMemory)
            .join(StrategyMemory, StrategyMemory.strategy_id == AIStrategy.strategy_id)
            .filter(AIStrategy.status.in_(["active", "running", "paused"]))
            .limit(30)
            .all()
        )
        for strat, mem in rows:
            if (mem.total_trades or 0) < 10:
                continue
            cid = f"strategy_{strat.strategy_id}"
            if cid in {c.candidate_id for c in out}:
                continue
            _, wr, dd, sharpe, rets = _strategy_metrics(db, strat.strategy_id)
            out.append(PromotionMetrics(
                candidate_id=cid,
                domain="strategy_evolution",
                stage=_stage_enum(cid),
                sharpe=sharpe,
                win_rate=wr,
                max_drawdown=dd,
                trade_count=int(mem.total_trades or 0),
                returns=rets if len(rets) >= 5 else None,
            ))
    except Exception as exc:
        logger.debug("[PromotionScan] 策略候选跳过: %s", exc)

    return out


def _scan_worker(session_id: str, tick: int) -> None:
    global _LAST_SCAN_MONO
    from backend.services.promotion_gate_service import is_enabled, scan_and_promote

    if not is_enabled():
        return

    try:
        with _LOCK:
            _STATS["in_flight"] = True
            _STATS["last_error"] = ""

        from backend.database.connection import SessionLocal

        db = SessionLocal()
        try:
            candidates = collect_candidates(db)
            decisions = scan_and_promote(candidates)
            promoted = 0
            for d in decisions:
                if not d.approved:
                    continue
                promoted += 1
                try:
                    from backend.services.runtime_governor import runtime_governor
                    for p in runtime_governor.list_pending():
                        keys = p.get("keys") or {}
                        if (
                            keys.get("_patch_type") == "promotion_gate"
                            and keys.get("candidate_id") == d.candidate_id
                            and keys.get("to_stage") == d.to_stage.value
                        ):
                            runtime_governor.approve(p.get("patch_id", ""))
                            break
                except Exception as appr_exc:
                    logger.debug("[PromotionScan] auto-approve 跳过: %s", appr_exc)

            with _LOCK:
                _STATS["last_scan_ts"] = time.time()
                _STATS["last_candidates"] = len(candidates)
                _STATS["last_promoted"] = promoted
            _LAST_SCAN_MONO = time.monotonic()
            if candidates:
                logger.info(
                    "[PromotionScan] tick=%d session=%s 候选=%d 晋升=%d",
                    tick, (session_id or "")[:12], len(candidates), promoted,
                )
        finally:
            db.close()
    except Exception as exc:
        with _LOCK:
            _STATS["last_error"] = str(exc)[:200]
        logger.warning("[PromotionScan] 扫描异常: %s", exc)
    finally:
        with _LOCK:
            _STATS["in_flight"] = False


def run_promotion_scan_tick(
    session_id: str = "",
    tick: int = 0,
    *,
    is_maintenance: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    """learning_loop 入口：维护周期离峰异步扫描。"""
    from backend.services.promotion_gate_service import is_enabled
    from backend.services.resource_guard import guard_training_operation, run_off_peak

    if not is_enabled():
        return {"ok": False, "skipped": True, "reason": "PROMOTION_GATE_ENABLED=false"}

    debounce = max(600, int(os.environ.get("PROMOTION_SCAN_DEBOUNCE_SEC", "1800")))
    if not force and not is_maintenance and (time.monotonic() - _LAST_SCAN_MONO) < debounce:
        return {"ok": True, "skipped": True, "reason": "debounce"}

    with _LOCK:
        if _STATS["in_flight"]:
            return {"ok": True, "skipped": True, "reason": "in_flight"}

    def _job():
        _scan_worker(session_id, tick)

    if not guard_training_operation("promotion_scan"):
        run_off_peak(_job, name="promotion-scan")
        return {"ok": True, "started": True, "deferred": True}

    threading.Thread(target=_job, daemon=True, name="promotion-scan").start()
    return {"ok": True, "started": True}
