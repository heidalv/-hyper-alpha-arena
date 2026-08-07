"""TrainingOrchestrator — 窄训练期全自动中枢。"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

JOB_REBALANCE = "training_portfolio_rebalance"
JOB_GRADUATION = "training_graduation_scan"
JOB_GOLDEN = "training_golden_sync"
JOB_CHAMPION = "training_champion_recovery"
JOB_LIVE_PROMOTE = "training_live_promote_scan"
JOB_LIVE_DEMOTE = "training_live_demote_scan"
JOB_VALIDATED_MERGE = "training_validated_merge"
JOB_AUDIT = "training_audit_report"

_booted = False


def _get_running_session(db):
    from backend.database.models import FullAutoSession

    return db.query(FullAutoSession).filter(FullAutoSession.status == "running").first()


def _strategy_tier(strat) -> str:
    tier = getattr(strat, "timeframe_tier", None)
    if tier:
        return str(tier)
    genome = getattr(strat, "genome", None) or {}
    nature = genome.get("trade_nature", "") if isinstance(genome, dict) else ""
    return {"scalp": "short", "swing": "mid", "position": "long"}.get(nature, "mid")


def _score_strategy(strat, mem) -> Tuple[int, float, float]:
    trades = int(mem.total_trades or 0) if mem else 0
    if mem:
        pnl = float(getattr(mem, "partial_pnl", 0) or 0)
        if pnl == 0 and (mem.avg_profit or mem.avg_loss):
            wins = int(trades * (mem.win_rate or 0))
            losses = max(0, trades - wins)
            pnl = wins * float(mem.avg_profit or 0) + losses * float(mem.avg_loss or 0)
    else:
        pnl = 0.0
    updated = 0.0
    if getattr(strat, "updated_at", None):
        try:
            updated = strat.updated_at.timestamp()
        except Exception:
            pass
    return trades, pnl, updated


def rebalance_portfolio(db) -> Dict[str, Any]:
    from backend.database.models import AIStrategy, StrategyMemory
    from backend.database.connection import sqlite_write_commit
    from backend.services.training_phase_service import (
        is_active,
        target_symbols,
        max_active_strategies,
        save_state,
        load_state,
    )
    from backend.services.training_audit import log_training_event

    if not is_active():
        return {"skipped": "training_inactive"}

    session = _get_running_session(db)
    if not session:
        return {"skipped": "no_session"}

    symbols = target_symbols()
    state = load_state()
    state["symbols"] = symbols
    save_state(state)

    # 2026-07-20：rebalance 不得覆盖 session.symbols。
    # 原实现 session.symbols = symbols 会把用户手动删除的币种（如 BNB/ASTER）
    # 在每次 rebalance 或重启后重新加回来，导致"删除后刷新又出现"的 bug。
    # rebalance 的职责是限制策略数量 + 暂停不在目标列表的策略，不应改 session 配置。
    # 这里用 session.symbols 和 target_symbols 的交集作为"有效 symbols"，
    # 既尊重用户的删除操作，又能暂停不在目标列表的策略。
    _session_syms = {str(s).upper() for s in (getattr(session, "symbols", None) or [])}
    _target_syms = {str(s).upper() for s in symbols}
    effective_symbols = list(_session_syms & _target_syms)
    # 如果交集为空（用户删了所有 target），退回 session.symbols，避免误暂停所有策略
    if not effective_symbols:
        effective_symbols = list(_session_syms)
    symbols = [str(s).upper() for s in effective_symbols]
    acct = session.paper_account_id or session.account_id
    strats = db.query(AIStrategy).filter(
        AIStrategy.account_id == acct,
        AIStrategy.status.in_(["active", "paused"]),
    ).all()

    paused = 0
    by_slot: Dict[str, List[Any]] = {}
    for strat in strats:
        sym = (strat.primary_symbol or "").upper()
        if sym not in symbols:
            if strat.status == "active":
                strat.status = "paused"
                # 2026-06-18: 标记 pause 原因，防止 _paper_auto_unlock_session
                # 无差别恢复（导致锁→解循环刷屏）。标 training_rebalance 后
                # _should_skip_revive 会跳过这些策略。
                genome = strat.genome if isinstance(strat.genome, dict) else {}
                genome["pause_reason"] = "training_rebalance"
                strat.genome = genome
                # 2026-06-19: 统一注册到 SymbolLockRegistry
                try:
                    from backend.services.symbol_lock_registry import lock_registry
                    lock_registry.lock(sym, strategy_id=str(strat.strategy_id),
                                       reason_code="training_rebalance", by="training_orch")
                except Exception:
                    pass
                paused += 1
            continue
        slot = f"{sym}:{_strategy_tier(strat)}"
        by_slot.setdefault(slot, []).append(strat)

    for slot, group in by_slot.items():
        if len(group) <= 1:
            continue
        scored = []
        for s in group:
            mem = db.query(StrategyMemory).filter(
                StrategyMemory.strategy_id == s.strategy_id
            ).first()
            scored.append((s, _score_strategy(s, mem)))
        scored.sort(key=lambda x: (x[1][0], x[1][1], x[1][2]), reverse=True)
        for s, _ in scored[1:]:
            if s.status == "active":
                s.status = "paused"
                genome = s.genome if isinstance(s.genome, dict) else {}
                genome["pause_reason"] = "training_rebalance"
                s.genome = genome
                paused += 1

    active = [s for s in strats if s.status == "active" and (s.primary_symbol or "").upper() in symbols]
    cap = max_active_strategies()
    if len(active) > cap:
        scored_all = []
        for s in active:
            mem = db.query(StrategyMemory).filter(
                StrategyMemory.strategy_id == s.strategy_id
            ).first()
            scored_all.append((s, _score_strategy(s, mem)))
        scored_all.sort(key=lambda x: (x[1][0], x[1][1]), reverse=True)
        for s, _ in scored_all[cap:]:
            s.status = "paused"
            paused += 1

    sqlite_write_commit(db)
    log_training_event("portfolio_rebalance", paused=paused, symbols=symbols, cap=cap)
    return {"paused": paused, "symbols": symbols, "active_cap": cap}


def sync_golden_tags(db) -> Dict[str, Any]:
    from backend.database.models import AIStrategy, StrategyMemory
    from backend.database.connection import sqlite_write_commit
    from backend.services.training_phase_service import is_active, get_graduation_status, load_state, save_state

    if not is_active():
        return {"skipped": "training_inactive"}

    tagged = 0
    state = load_state()
    windows = state.setdefault("champion_windows", {})

    def _is_champion(mem) -> bool:
        if not mem:
            return False
        return (
            (mem.total_trades or 0) >= 15
            and (mem.win_rate or 0) >= 0.55
            and (mem.sharpe_ratio or 0) >= 0.5
            and (mem.max_drawdown or 1.0) <= 0.15
        )

    acct_strats = db.query(AIStrategy).filter(
        AIStrategy.status.in_(["active", "paused"])
    ).all()
    for strat in acct_strats:
        mem = db.query(StrategyMemory).filter(
            StrategyMemory.strategy_id == strat.strategy_id
        ).first()
        should_tag = False
        if get_graduation_status(strat.strategy_id) == "graduated":
            should_tag = True
        if mem and _is_champion(mem):
            sid = strat.strategy_id
            windows[sid] = int(windows.get(sid) or 0) + 1
            if windows[sid] >= 2:
                should_tag = True

        genome = dict(strat.genome or {})
        tags = list(genome.get("tags") or [])
        if should_tag and "golden_frozen" not in tags:
            tags.append("golden_frozen")
            genome["tags"] = tags
            strat.genome = genome
            strat.learning_enabled = False
            tagged += 1

    save_state(state)
    if tagged:
        sqlite_write_commit(db)
    return {"tagged": tagged}


def run_validated_merge(db) -> Dict[str, Any]:
    from backend.database.models import OpenCodeEvolutionProposalDB
    from backend.database.connection import sqlite_write_commit
    from backend.services.runtime_tuning_store import merge_overlay_to_global, rollback_snapshot, remove_overlay
    from backend.services.decision_policy_engine import rollback_policy_snapshot
    from backend.services.training_audit import log_training_event
    from backend.services.training_phase_service import is_active

    if not is_active():
        return {"skipped": "training_inactive"}

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    merged = 0
    rolled = 0
    overfit_blocked = 0  # P1: 过拟合拦截计数

    rows = db.query(OpenCodeEvolutionProposalDB).filter(
        OpenCodeEvolutionProposalDB.status.in_(["paper_validated", "paper_applying"])
    ).all()

    for row in rows:
        try:
            after = json.loads(row.after_json or "{}")
        except Exception:
            after = {}

        # ── P1: 过拟合检测 ──
        overfit_risk = _detect_overfitting_risk(after)
        if overfit_risk["is_overfit"]:
            logger.warning(
                f"[TrainingOrch] proposal {row.id} 检测到过拟合风险: "
                f"score={overfit_risk['score']:.2f} "
                f"reasons={overfit_risk['reasons']}"
            )
            log_training_event(
                "overfit_blocked",
                proposal_id=row.id,
                overfit_score=overfit_risk["score"],
                reasons=overfit_risk["reasons"],
            )
            # 过拟合严重时直接回滚
            if overfit_risk["score"] >= 0.8:
                rollback_snapshot(row.id)
                rollback_policy_snapshot(row.id)
                remove_overlay(row.id)
                row.status = "rolled_back"
                row.status_reason = f"overfit_detected: {', '.join(overfit_risk['reasons'])}"
                overfit_blocked += 1
                rolled += 1
                continue
            # 中等风险时降级为 neutral 等待更多验证
            elif overfit_risk["score"] >= 0.5:
                overfit_blocked += 1
                continue  # 本轮跳过，等待更多样本验证

        verdict = after.get("verdict")
        if row.status == "paper_validated":
            if verdict == "improved" or verdict is None:
                result = merge_overlay_to_global(row.id)
                log_training_event(
                    "validated_merge",
                    proposal_id=row.id,
                    verdict=verdict,
                    merged=result.get("merged"),
                    patches=result.get("patches"),
                )
                merged += 1
            elif verdict == "neutral":
                applied_at = row.applied_at
                if applied_at and applied_at.tzinfo is not None:
                    applied_at = applied_at.replace(tzinfo=None)
                age_days = (now - applied_at).total_seconds() / 86400.0 if applied_at else 0
                if age_days >= 7:
                    merge_overlay_to_global(row.id)
                    log_training_event("neutral_auto_merge", proposal_id=row.id, age_days=age_days)
                    merged += 1
            elif verdict == "degraded" and row.status == "paper_validated":
                rollback_snapshot(row.id)
                rollback_policy_snapshot(row.id)
                remove_overlay(row.id)
                row.status = "rolled_back"
                log_training_event("auto_rollback", proposal_id=row.id)
                rolled += 1

    if merged or rolled or overfit_blocked:
        sqlite_write_commit(db)
    return {"merged": merged, "rolled_back": rolled, "overfit_blocked": overfit_blocked}


# ══════════════════════════════════════════════════════
#  P1: 过拟合风险检测辅助函数 (M-10)
# ══════════════════════════════════════════════════════

def _detect_overfitting_risk(after: Dict[str, Any]) -> Dict[str, Any]:
    """
    检测回测验证结果中的过拟合风险。

    检查维度:
    1. 样本外衰减: out_sample_sharpe vs in_sample_sharpe 的比值
    2. 交易次数过少: 样本量不足导致统计不显著
    3. 过度优化迹象: 参数数量 vs 样本数量比例
    4. 极端表现: win_rate > 0.9 或 sharpe > 5 (几乎不可能在实盘持续)

    Returns:
        {"is_overfit": bool, "score": float(0-1), "reasons": [str, ...]}
    """
    risk_score = 0.0
    reasons = []

    try:
        # 1. 样本外衰减检测
        in_sample_sharpe = after.get("in_sample_sharpe") or after.get("backtest_sharpe") or 0
        out_sample_sharpe = after.get("out_sample_sharpe") or after.get("walk_forward_sharpe")
        if out_sample_sharpe is not None and in_sample_sharpe > 0:
            decay_ratio = out_sample_sharpe / max(in_sample_sharpe, 0.01)
            if decay_ratio < 0.3:  # 样本外衰减超过70%
                risk_score += 0.4
                reasons.append(f"样本外Sharpe衰减{1-decay_ratio:.0%}")
            elif decay_ratio < 0.5:
                risk_score += 0.2
                reasons.append(f"样本外Sharpe衰减{1-decay_ratio:.0%}")

        # 2. 交易次数过少
        total_trades = after.get("total_trades") or after.get("backtest_total_trades") or 0
        param_count = after.get("param_count") or after.get("optimized_params_count") or 0
        if total_trades < 30:
            risk_score += 0.3
            reasons.append(f"交易次数过少({total_trades}笔)")
        elif total_trades < 50:
            risk_score += 0.15
            reasons.append(f"交易次数偏少({total_trades}笔)")

        # 3. 过度优化: 参数/样本比例
        if param_count > 0 and total_trades > 0:
            param_trade_ratio = param_count / total_trades
            if param_trade_ratio > 0.3:
                risk_score += 0.3
                reasons.append(f"参数/样本比过高({param_trade_ratio:.2f})")
            elif param_trade_ratio > 0.15:
                risk_score += 0.15
                reasons.append(f"参数/样本比偏高({param_trade_ratio:.2f})")

        # 4. 极端表现检测
        win_rate = after.get("win_rate") or after.get("backtest_win_rate") or 0
        if win_rate > 0.9:
            risk_score += 0.3
            reasons.append(f"胜率过高({win_rate:.0%})，可能过拟合")
        if (after.get("sharpe_ratio") or after.get("backtest_sharpe") or 0) > 5:
            risk_score += 0.25
            reasons.append("Sharpe>5，实盘几乎不可能复现")

        # 5. 最大回撤异常小
        max_dd = after.get("max_drawdown") or after.get("backtest_max_drawdown")
        if max_dd is not None and max_dd < 0.02 and win_rate < 0.7:
            risk_score += 0.15
            reasons.append("最大回撤异常小，可能存在未来函数")

    except Exception as e:
        logger.debug(f"[TrainingOrch] 过拟合检测异常: {e}")

    risk_score = min(1.0, risk_score)
    return {
        "is_overfit": risk_score >= 0.5,
        "score": round(risk_score, 2),
        "reasons": reasons,
    }


def run_audit_report(db) -> Dict[str, Any]:
    from backend.services.training_audit import REPORT_DIR, TRAINING_AUDIT_FILE, LIVE_AUDIT_FILE
    from backend.services.training_phase_service import status_snapshot
    from backend.services.opencode_proposal_applier import evaluate_proposals_summary

    os.makedirs(REPORT_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    path = os.path.join(REPORT_DIR, f"daily_{ts}.json")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "training_phase": status_snapshot(),
        "funnel": evaluate_proposals_summary(db),
        "audit_file": TRAINING_AUDIT_FILE,
        "live_audit_file": LIVE_AUDIT_FILE,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return {"report": path}


def boot_training_phase() -> Dict[str, Any]:
    """启动时：TRAINING_PHASE_AUTO + running session → 激活训练期并 rebalance。"""
    global _booted
    from backend.config.settings import TRAINING_PHASE_AUTO
    from backend.database.connection import SessionLocal
    from backend.services.training_phase_service import load_state, save_state

    if not TRAINING_PHASE_AUTO:
        return {"booted": False, "reason": "TRAINING_PHASE_AUTO=false"}

    db = SessionLocal()
    try:
        session = _get_running_session(db)
        if not session:
            return {"booted": False, "reason": "no_running_session"}

        state = load_state()
        state["active"] = True
        if not state.get("started_at"):
            state["started_at"] = datetime.now(timezone.utc).isoformat()
        save_state(state)

        rebalance = rebalance_portfolio(db)
        _booted = True
        logger.info("[TrainingOrchestrator] boot active session=%s rebalance=%s", session.session_id, rebalance)
        return {"booted": True, "session_id": session.session_id, "rebalance": rebalance}
    finally:
        db.close()


# ══════════════════════════════════════════════════════
#  Phase 7: OpenCode 策略对比 — 竞争策略智能选择
# ══════════════════════════════════════════════════════

def _opencode_strategy_compare(
    db, strategy_a_id: str, strategy_b_id: str, symbol: str, tier: str,
) -> Dict[str, Any]:
    """
    让 OpenCode 对比两个竞争同一 slot 的策略，给出保留建议。
    仅当数值分数差距 <15% 时才触发，避免不必要的 API 调用。
    """
    try:
        from backend.services.opencode_bridge import (
            _is_enabled, _load_system_prompt, _agent_plan, _model,
            run_http_agent_message, _extract_json,
        )
        if not _is_enabled():
            return {"keep": strategy_a_id, "reason": "OpenCode disabled", "confidence": 0}

        from backend.database.models import StrategyMemory, StrategyTrade

        mem_a = db.query(StrategyMemory).filter(StrategyMemory.strategy_id == strategy_a_id).first()
        mem_b = db.query(StrategyMemory).filter(StrategyMemory.strategy_id == strategy_b_id).first()

        def _summarize(mem) -> Dict:
            if not mem:
                return {}
            return {
                "win_rate": float(mem.win_rate or 0),
                "total_trades": int(mem.total_trades or 0),
                "total_pnl": float(mem.total_pnl or 0),
                "avg_hold_min": float(mem.avg_hold_min or 0),
                "key_lessons": (mem.key_lessons or [])[-5:],
            }

        system = _load_system_prompt()
        user_text = (
            f"## 竞争策略对比\n\n"
            f"- Symbol: {symbol} | Tier: {tier}\n"
            f"- 策略A ({strategy_a_id}): {json.dumps(_summarize(mem_a), ensure_ascii=False)}\n"
            f"- 策略B ({strategy_b_id}): {json.dumps(_summarize(mem_b), ensure_ascii=False)}\n\n"
            f"这两个策略竞争同一个slot，请建议保留哪个。\n"
            f"输出JSON: {{\"keep\": \"A或B的strategy_id\", \"reason\": \"...\", \"confidence\": 0.0}}"
        )

        raw, err = run_http_agent_message(
            system_prompt=system,
            user_text=user_text,
            agent=_agent_plan(),
            model_slug=_model(),
            session_title=f"Strategy Compare: {strategy_a_id} vs {strategy_b_id}",
        )
        if err:
            return {"keep": strategy_a_id, "reason": f"OpenCode error: {err}", "confidence": 0}

        result = _extract_json(raw or "")
        logger.info(
            f"[TrainingOrch] OpenCode策略对比: keep={result.get('keep')} "
            f"conf={result.get('confidence', 0)}"
        )
        return result
    except Exception as exc:
        logger.debug("[TrainingOrch] 策略对比失败(非致命): %s", exc)
        return {"keep": strategy_a_id, "reason": str(exc)[:100], "confidence": 0}


def register_training_jobs() -> None:
    from backend.services.scheduler import task_scheduler
    from backend.database.connection import SessionLocal

    if not task_scheduler.is_running():
        task_scheduler.start()

    def _with_db(fn):
        db = SessionLocal()
        try:
            return fn(db)
        finally:
            db.close()

    # ── P1: 多频率训练调度函数 ──
    def _run_freq_15m_review():
        """15m 频率周期复评 (每6h)"""
        db = SessionLocal()
        try:
            from backend.services.strategy_learning_service import strategy_learning
            from backend.database.models import AIStrategy
            strategies = db.query(AIStrategy).filter(
                AIStrategy.status.in_(["active", "paused"])
            ).all()
            for s in strategies:
                try:
                    strategy_learning.run_periodic_review_by_freq(s.strategy_id, freq="15m", days=3)
                except Exception as e:
                    logger.debug(f"[TrainingOrch] 15m复评 {s.strategy_id}: {e}")
        except Exception as e:
            logger.error(f"[TrainingOrch] 15m批量复评失败: {e}")
        finally:
            db.close()

    def _run_freq_1h_review():
        """1h 频率周期复评 (每12h)"""
        db = SessionLocal()
        try:
            from backend.services.strategy_learning_service import strategy_learning
            from backend.database.models import AIStrategy
            strategies = db.query(AIStrategy).filter(
                AIStrategy.status.in_(["active", "paused"])
            ).all()
            for s in strategies:
                try:
                    strategy_learning.run_periodic_review_by_freq(s.strategy_id, freq="1h", days=7)
                except Exception as e:
                    logger.debug(f"[TrainingOrch] 1h复评 {s.strategy_id}: {e}")
        except Exception as e:
            logger.error(f"[TrainingOrch] 1h批量复评失败: {e}")
        finally:
            db.close()

    def _run_freq_4h_review():
        """4h 频率周期复评 (每24h)"""
        db = SessionLocal()
        try:
            from backend.services.strategy_learning_service import strategy_learning
            from backend.database.models import AIStrategy
            strategies = db.query(AIStrategy).filter(
                AIStrategy.status.in_(["active", "paused"])
            ).all()
            for s in strategies:
                try:
                    strategy_learning.run_periodic_review_by_freq(s.strategy_id, freq="4h", days=14)
                except Exception as e:
                    logger.debug(f"[TrainingOrch] 4h复评 {s.strategy_id}: {e}")
        except Exception as e:
            logger.error(f"[TrainingOrch] 4h批量复评失败: {e}")
        finally:
            db.close()

    def _run_drift_detection():
        """概念漂移批量检测 (每6h)"""
        db = SessionLocal()
        try:
            from backend.services.strategy_learning_service import strategy_learning
            from backend.database.models import AIStrategy, StrategyTrade
            strategies = db.query(AIStrategy).filter(
                AIStrategy.status.in_(["active", "paused"])
            ).all()
            drift_count = 0
            for s in strategies:
                try:
                    recent = db.query(StrategyTrade).filter(
                        StrategyTrade.strategy_id == s.strategy_id,
                        StrategyTrade.pnl_pct.isnot(None),
                    ).order_by(StrategyTrade.opened_at.desc()).limit(50).all()
                    if len(recent) >= 10:
                        result = strategy_learning._detect_concept_drift(
                            db, s.strategy_id, recent_trades=recent
                        )
                        if result.get("drift_detected"):
                            drift_count += 1
                            logger.warning(
                                f"[TrainingOrch] 漂移告警: {s.strategy_id} "
                                f"severity={result['drift_severity']} "
                                f"action={result['recommended_action']}"
                            )
                except Exception as e:
                    logger.debug(f"[TrainingOrch] 漂移检测 {s.strategy_id}: {e}")
            if drift_count > 0:
                logger.info(f"[TrainingOrch] 本轮漂移检测: {drift_count} 个策略出现漂移")
        except Exception as e:
            logger.error(f"[TrainingOrch] 批量漂移检测失败: {e}")
        finally:
            db.close()

    jobs = [
        (lambda: _with_db(rebalance_portfolio), 21600, JOB_REBALANCE),
        (lambda: _with_db(lambda db: __import__(
            "backend.services.training_graduation_service", fromlist=["scan_graduation"]
        ).scan_graduation(db)), 21600, JOB_GRADUATION),
        (lambda: _with_db(sync_golden_tags), 3600, JOB_GOLDEN),
        (lambda: _with_db(lambda db: __import__(
            "backend.services.champion_recovery_service", fromlist=["run_champion_recovery"]
        ).run_champion_recovery(db)), 600, JOB_CHAMPION),
        (lambda: _with_db(run_validated_merge), 3600, JOB_VALIDATED_MERGE),
        (lambda: _with_db(lambda db: __import__(
            "backend.services.training_live_promote_service", fromlist=["scan_live_promote"]
        ).scan_live_promote(db)), 21600, JOB_LIVE_PROMOTE),
        (lambda: _with_db(lambda db: __import__(
            "backend.services.training_live_promote_service", fromlist=["scan_live_demote"]
        ).scan_live_demote(db)), 3600, JOB_LIVE_DEMOTE),
        (lambda: _with_db(run_audit_report), 86400, JOB_AUDIT),
        # ── P1: 多频率训练作业 ──
        (_run_freq_15m_review, 21600, "training_freq_15m_review"),
        (_run_freq_1h_review, 43200, "training_freq_1h_review"),
        (_run_freq_4h_review, 86400, "training_freq_4h_review"),
        (_run_drift_detection, 21600, "training_drift_detection"),
    ]

    for func, interval, jid in jobs:
        try:
            if task_scheduler.scheduler and task_scheduler.scheduler.get_job(jid):
                task_scheduler.remove_task(jid)
            task_scheduler.add_interval_task(task_func=func, interval_seconds=interval, task_id=jid)
            logger.info("[TrainingOrchestrator] 注册 %s 每 %ds", jid, interval)
        except Exception as err:
            logger.error("[TrainingOrchestrator] %s 失败: %s", jid, err)

    boot_training_phase()
