"""训练期 Live 自动晋升 / 降级。"""

from __future__ import annotations

import json
import logging
import numpy as np
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


def compute_real_trade_metrics(db, strategy_id: str) -> Dict[str, float]:
    """[P0-3 口径修复] 从 strategy_trades 计算真指标。

    返回：
      real_sharpe    — 交易级收益率的年化 Sharpe（年化按平均持仓时长换算，
                       替代 StrategyMemory.sharpe_ratio 的盈亏符号 EMA，
                       后者值域 [-1,1] 无法与 Gate2 的 1.0 阈值比较）；
      equity_dd_pct  — 累计收益率曲线（cumprod(1+pnl_pct)-1）的峰谷回撤（%），
                       替代 mem.max_drawdown（实际是单笔最大亏损）；
      twm_return_pct — 已实现盈亏 ÷ 时间加权平均占用保证金 × 100（%），
                       替代 pnl ÷ 累计换手名义额（维度错配，高换手必被误拦）；
      n_trades       — 样本数。
    """
    from backend.database.models import StrategyTrade
    try:
        rows = (
            db.query(StrategyTrade)
            .filter(StrategyTrade.strategy_id == strategy_id,
                    StrategyTrade.status == "closed")
            .order_by(StrategyTrade.closed_at.asc())
            .all()
        )
        pcts = [float(r.pnl_pct) for r in rows if r.pnl_pct is not None]
        n = len(pcts)
        real_sharpe = 0.0
        if n >= 5:
            _mean = float(np.mean(pcts))
            _std = float(np.std(pcts))
            if _std > 1e-10:
                # 年化按平均持仓时长：avg_hold_hours → 年化期数 365*24/avg_hold_hours
                _holds = [
                    float(r.holding_period or 0) / 3600.0
                    for r in rows if (r.holding_period or 0) > 0
                ]
                _avg_h = float(np.mean(_holds)) if _holds else 24.0
                _ann = max(10.0, min(8760.0, 365.0 * 24.0 / max(_avg_h, 1e-6)))
                real_sharpe = _mean / _std * float(np.sqrt(_ann))
        equity_dd_pct = 0.0
        if n >= 5:
            _curve = np.cumprod(1.0 + np.array(pcts, dtype=float)) - 1.0
            _peak = np.maximum.accumulate(_curve)
            equity_dd_pct = float(np.max(_peak - _curve)) * 100.0
        # 时间加权平均占用保证金（margin_i = entry×size/leverage，权重=持仓时长）
        _tw_num = 0.0
        _tw_den = 0.0
        _total_pnl = 0.0
        for r in rows:
            _pnl = float(r.pnl or 0.0)
            _total_pnl += _pnl
            _entry = float(r.entry_price or 0)
            _size = float(r.position_size or 0)
            _lev = float(r.leverage or 1.0) or 1.0
            _dur = float(r.holding_period or 0)
            if _entry > 0 and _size > 0 and _dur > 0:
                _margin = _entry * _size / _lev
                _tw_num += _margin * _dur
                _tw_den += _dur
        twm_return_pct = 0.0
        if _tw_den > 0 and _tw_num > 0:
            twm_return_pct = _total_pnl / (_tw_num / _tw_den) * 100.0
        return {
            "real_sharpe": round(real_sharpe, 4),
            "equity_dd_pct": round(equity_dd_pct, 4),
            "twm_return_pct": round(twm_return_pct, 4),
            "n_trades": n,
        }
    except Exception as e:
        logger.debug("[LivePromote] 真指标计算失败 %s: %s", strategy_id, e)
        return {"real_sharpe": 0.0, "equity_dd_pct": 0.0, "twm_return_pct": 0.0, "n_trades": 0}


def _compute_paper_total_return_pct(db, strategy_id: str, base_size: float) -> float:
    """从 strategy_trades 汇总真实已实现盈亏，换算成收益率(%)。

    [2026-07-18 修复] 此前 `_run_gate2` 里 total_return_pct 硬编码为 0.0，导致
    Gate2 "模拟盘与回测收益偏差 ≤30%" 这条一致性检查恒为 0 偏差——不管模拟盘
    实际表现和回测预期差多远都能通过，这条门槛形同虚设。这里改为从
    `strategy_trades`（已平仓、真实 pnl）里汇总真实收益。

    [2026-08-15 口径修复] 分母原用 `max_position_size`（0~1 的无单位仓位比例），
    pnl(USD) / 0.2 得到的是无意义的「金额/比例」值，与回测收益率(%)维度不对齐。
    现分母改为已平仓交易的成交名义额之和（真实资金占用），即
    「已实现盈亏 / 成交名义额」，并在 details 中标注口径（return_basis）。
    """
    from backend.database.models import StrategyTrade
    try:
        rows = db.query(StrategyTrade).filter(
            StrategyTrade.strategy_id == strategy_id,
            StrategyTrade.status == "closed",
        ).all()
        total_pnl = sum(float(r.pnl or 0.0) for r in rows)
        traded_notional = 0.0
        for r in rows:
            entry = float(getattr(r, "entry_price", 0) or 0)
            qty = float(getattr(r, "quantity", 0) or 0)
            if entry > 0 and qty > 0:
                traded_notional += entry * qty
        base = traded_notional if traded_notional > 0 else 0.0
        if base <= 0:
            # 无真实成交名义额 → 无法换算收益口径（诚实返回 0 并由
            # Gate2 的 fail-closed 分支拦截，不再用仓位比例硬除）。
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
    # [P0-3 口径修复] 用 strategy_trades 现算真指标：
    #  real_sharpe（年化，按平均持仓时长）替代盈亏符号 EMA；
    #  equity_dd_pct（累计收益曲线回撤）替代"单笔最大亏损"；
    #  twm_return_pct（盈亏 ÷ 时间加权平均占用保证金）替代 pnl/换手额。
    _real = compute_real_trade_metrics(db, strat.strategy_id)
    total_return_pct = _real["twm_return_pct"]

    metrics = PaperTradingMetrics(
        days_running=int(days_running),
        total_trades=int(mem.total_trades or 0),
        # 旧字段保留：mem.max_drawdown 是单笔最大亏损，作为辅助门槛（≤15%）继续用
        sharpe_ratio=0.0,  # 旧伪 Sharpe 不再参与 Gate2 判定
        max_drawdown_pct=float((mem.max_drawdown or 0.0) * 100.0),
        real_sharpe=_real["real_sharpe"],
        equity_dd_pct=_real["equity_dd_pct"],
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
        n_trades = int(mem.total_trades or 0) if mem else 0
        mc_ratio = 0.0
        if mem and n_trades > 0:
            mc_ratio = float(getattr(mem, "master_close_loss_ratio", 0) or 0)

        # [P1-2 判据修复] 原 wr<0.40 裸胜率判据误杀低胜率高赔率策略（30% 胜率 + RR3:1
        # 是正期望）。改期望值判据：n≥30 且 E=wr×avg_profit+(1-wr)×avg_loss < 0 才降级，
        # 样本不足时只按回撤/总控连亏降级（fail-safe）。
        ev = 0.0
        if mem and n_trades >= 30:
            _ap = float(getattr(mem, "avg_profit", 0) or 0)
            _al = float(getattr(mem, "avg_loss", 0) or 0)
            ev = wr * _ap + (1.0 - wr) * _al
        should_demote = (n_trades >= 30 and ev < 0) or dd > 0.18 or mc_ratio > 0.55
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
        # [P1-2 粒度修复] 删除 session.trading_mode = "paper"：单策略降级曾把整个会话
        # 切回模拟盘，其余正常 live 策略被连坐停真金。降级按策略粒度（live_stage=none）。
        log_live_event("demote_to_paper", strategy_id=strat.strategy_id, wr=wr, dd=dd, ev=ev)
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
