"""训练期 Live 自动晋升 / 降级。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _live_strategies(db, acct_id: int) -> List[Any]:
    from backend.database.models import AIStrategy

    rows = db.query(AIStrategy).filter(AIStrategy.account_id == acct_id).all()
    out = []
    for s in rows:
        genome = s.genome or {}
        if isinstance(genome, dict) and genome.get("live_stage") in ("probe", "normal", "full"):
            out.append(s)
    return out


def _wallet_env_ok(db, session) -> bool:
    from backend.config.settings import TRAINING_LIVE_ENV
    from backend.database.models import Account

    acct = db.query(Account).filter(Account.id == session.account_id).first()
    env = (getattr(acct, "hyperliquid_environment", None) or "testnet").lower()
    target = (TRAINING_LIVE_ENV or "mainnet").lower()
    if target == "mainnet" and env != "mainnet":
        return False
    return env == target or target == "testnet"


def scan_live_promote(db) -> Dict[str, Any]:
    from backend.config.settings import (
        TRAINING_AUTO_LIVE,
        TRAINING_LIVE_MAX_STRATEGIES,
        TRAINING_LIVE_PROBE_SIZE_MULT,
    )
    from backend.database.models import AIStrategy, StrategyMemory, FullAutoSession, OpenCodeEvolutionProposalDB
    from backend.services.training_phase_service import load_state, dequeue_graduation
    from backend.services.training_audit import log_live_event
    from backend.services.runtime_governor import runtime_governor

    if not TRAINING_AUTO_LIVE:
        return {"skipped": "TRAINING_AUTO_LIVE=false"}

    session = db.query(FullAutoSession).filter(FullAutoSession.status == "running").first()
    if not session:
        return {"skipped": "no_session"}
    if not _wallet_env_ok(db, session):
        log_live_event("promote_skipped_env_mismatch", session_id=session.session_id)
        return {"skipped": "env_mismatch"}

    acct = session.paper_account_id or session.account_id
    live_count = len(_live_strategies(db, acct))
    if live_count >= TRAINING_LIVE_MAX_STRATEGIES:
        return {"skipped": "live_cap", "live_count": live_count}

    queue = list(load_state().get("graduation_queue") or [])
    promoted = 0
    pending_confirm: List[Dict[str, Any]] = []
    for sid in queue:
        if live_count + promoted >= TRAINING_LIVE_MAX_STRATEGIES:
            break
        strat = db.query(AIStrategy).filter(AIStrategy.strategy_id == sid).first()
        if not strat:
            dequeue_graduation(sid)
            continue
        genome = dict(strat.genome or {})
        tags = genome.get("tags") or []
        if "golden_frozen" not in tags:
            continue
        if genome.get("live_stage") in ("probe", "normal", "full"):
            dequeue_graduation(sid)
            continue

        mem = db.query(StrategyMemory).filter(StrategyMemory.strategy_id == sid).first()
        if not mem or (mem.total_trades or 0) < 30:
            continue
        if (mem.win_rate or 0) < 0.48 or (mem.max_drawdown or 0) > 0.15:
            continue

        merged = db.query(OpenCodeEvolutionProposalDB).filter(
            OpenCodeEvolutionProposalDB.status == "paper_validated"
        ).first()
        if not merged:
            continue

        # ── 阶段三 3.1：Gate2 硬门槛（模拟运行≥14天 / Sharpe≥1.0 / 回撤≤10% /
        # 笔数≥30 / 与回测偏差≤30%）。任何一项不达标直接跳过，不进待确认队列。──
        gate2 = _run_gate2(db, strat, mem, session, merged)
        if not gate2.passed:
            log_live_event(
                "promote_blocked_gate2",
                strategy_id=sid,
                proposal_id=merged.id,
            )
            logger.info("[LivePromote] %s 未过 Gate2: %s", sid, gate2.failed_checks)
            continue

        # ── 阶段三 3.2：真金零自动切换。达标策略只进人工确认队列，
        # 由 RuntimeGovernor 人工 approve 后才真正切真金小仓。──
        if runtime_governor.has_pending_live_promote(sid):
            # 已在待确认队列，避免重复提交；保持在毕业队列等人工处理。
            continue

        base_size = float(strat.max_position_size or 0.2)
        patch = runtime_governor.propose_live_promote(
            strategy_id=sid,
            proposal_id=merged.id,
            session_id=session.session_id,
            base_size=base_size,
            size_mult=TRAINING_LIVE_PROBE_SIZE_MULT,
            gate2_details=gate2.details,
            reason=f"模拟盘 Gate2 通过，待人工确认真金晋升 {sid}",
        )
        log_live_event(
            "promote_pending_manual_confirm",
            strategy_id=sid,
            proposal_id=merged.id,
            patch_id=patch.patch_id,
        )
        pending_confirm.append({"strategy_id": sid, "patch_id": patch.patch_id})

    return {
        "promoted": 0,
        "pending_confirm": pending_confirm,
        "live_count": live_count,
    }


def _compute_paper_total_return_pct(db, strategy_id: str, base_size: float) -> float:
    """从 strategy_trades 汇总真实已实现盈亏，换算成相对策略基础仓位的收益率(%)。

    [2026-07-18 修复] 此前 `_run_gate2` 里 total_return_pct 硬编码为 0.0，导致
    Gate2 "模拟盘与回测收益偏差 ≤30%" 这条一致性检查恒为 0 偏差——不管模拟盘
    实际表现和回测预期差多远都能通过，这条门槛形同虚设。这里改为从
    `strategy_trades`（已平仓、真实 pnl）里汇总真实收益。
    """
    from backend.database.models import StrategyTrade
    try:
        rows = db.query(StrategyTrade).filter(
            StrategyTrade.strategy_id == strategy_id,
            StrategyTrade.status == "closed",
        ).all()
        total_pnl = sum(float(r.pnl or 0.0) for r in rows)
        base = float(base_size) if base_size else 0.0
        if base <= 0:
            return 0.0
        return round((total_pnl / base) * 100.0, 4)
    except Exception as e:
        logger.debug("[LivePromote] total_return_pct 计算失败 %s: %s", strategy_id, e)
        return 0.0


def _run_gate2(db, strat, mem, session, merged):
    """用 StrategyMemory + 会话运行时长构造 Gate2 指标并执行硬门槛校验。"""
    from backend.services.strategy_validator import strategy_validator, PaperTradingMetrics

    days_running = 0
    try:
        genome = dict(strat.genome or {})
        started = None
        # 优先用策略自身进入模拟盘的时间，回退到会话开始时间
        for key in ("paper_started_at", "created_at"):
            val = genome.get(key)
            if val:
                started = val
                break
        if started is None and getattr(session, "started_at", None):
            started = session.started_at
        if isinstance(started, str):
            started = datetime.fromisoformat(started.replace("Z", "+00:00"))
        if isinstance(started, datetime):
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            days_running = max(0, (datetime.now(timezone.utc) - started).days)
    except Exception:
        days_running = 0

    # 与回测预期收益偏差：仅当两侧都有可信数据时才启用（否则置 0 跳过该软项）
    backtest_return_pct = 0.0
    try:
        genome = dict(strat.genome or {})
        backtest_return_pct = float(genome.get("backtest_return_pct") or 0.0)
    except Exception:
        pass

    base_size = float(strat.max_position_size or 0.2)
    total_return_pct = _compute_paper_total_return_pct(db, strat.strategy_id, base_size)

    metrics = PaperTradingMetrics(
        days_running=int(days_running),
        total_trades=int(mem.total_trades or 0),
        sharpe_ratio=float(mem.sharpe_ratio or 0.0),
        max_drawdown_pct=float((mem.max_drawdown or 0.0) * 100.0),
        total_return_pct=total_return_pct,
        backtest_return_pct=backtest_return_pct,
    )
    return strategy_validator.validate_gate2(metrics)


def scan_live_demote(db) -> Dict[str, Any]:
    from backend.database.models import AIStrategy, StrategyMemory, FullAutoSession
    from backend.database.connection import sqlite_write_commit
    from backend.services.training_audit import log_live_event
    from backend.services.runtime_tuning_store import rollback_snapshot

    session = db.query(FullAutoSession).filter(FullAutoSession.status == "running").first()
    if not session:
        return {"skipped": "no_session"}

    acct = session.paper_account_id or session.account_id
    demoted = 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    for strat in _live_strategies(db, acct):
        mem = db.query(StrategyMemory).filter(
            StrategyMemory.strategy_id == strat.strategy_id
        ).first()
        wr = float(mem.win_rate or 0) if mem else 0
        dd = float(mem.max_drawdown or 0) if mem else 0
        mc_ratio = 0.0
        if mem and (mem.total_trades or 0) > 0:
            mc_ratio = float(getattr(mem, "master_close_loss_ratio", 0) or 0)

        should_demote = wr < 0.40 or dd > 0.18 or mc_ratio > 0.55
        if not should_demote:
            _advance_live_stage(strat, mem)
            continue

        genome = dict(strat.genome or {})
        base_size = genome.get("live_probe_base_size")
        if base_size is not None:
            strat.max_position_size = float(base_size)
        genome["live_stage"] = "none"
        genome["live_demoted_at"] = datetime.now(timezone.utc).isoformat()
        genome["live_cooldown_until"] = (datetime.now(timezone.utc) + timedelta(hours=72)).isoformat()
        strat.genome = genome
        session.trading_mode = "paper"
        log_live_event("demote_to_paper", strategy_id=strat.strategy_id, wr=wr, dd=dd)
        demoted += 1

    if demoted:
        sqlite_write_commit(db)
    return {"demoted": demoted}


def _advance_live_stage(strat, mem) -> None:
    genome = dict(strat.genome or {})
    stage = genome.get("live_stage")
    if stage == "probe" and mem and (mem.win_rate or 0) >= 0.45 and (mem.max_drawdown or 1) <= 0.12:
        base = genome.get("live_probe_base_size")
        if base is not None:
            strat.max_position_size = float(base)
        genome["live_stage"] = "normal"
        strat.genome = genome
    elif stage == "normal" and mem and (mem.total_trades or 0) >= 30:
        genome["live_stage"] = "full"
        if "champion_protected" not in (genome.get("tags") or []):
            tags = list(genome.get("tags") or [])
            tags.append("champion_protected")
            genome["tags"] = tags
        strat.genome = genome
