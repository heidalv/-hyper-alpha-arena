"""模拟盘会话辅助 — 从 monolith 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# 模块级状态记录：以 session_id 为 key,记录上次 _should_reset_loss_protection
# 观察到的 status。用于检测 paused→running 真实转换(根因1止血)。
# 注意必须用稳定的 session_id 字符串(不是 id(session)),因为 coordinator_loop
# 每个 tick 都新查出一个 ORM 对象,id() 每次都不同,会导致转换检测永远不命中。
# 不直接挂在 session 对象上,避免 MagicMock 拦截 __setattr__ / ORM 刷新丢字段。
_prev_loss_lock_status_map: dict = {}


def _should_reset_loss_protection(session) -> bool:
    """只有 session 从 paused→running 真实转换时才重置心态状态。

    普通 running tick 不重置,保护连亏降杠杆生效(根因1止血)。
    之前 paper_auto_unlock_session 每个 unified tick(~30s)在 _paper_loss_locks_disabled
    为真时无条件调 reset_loss_protection_state,会把 leverage_cap 抹回 20,
    导致连亏降杠杆 30s 内被擦掉永远攒不起来。

    注意:该门控只包住 reset_loss_protection_state 一个调用。paper_auto_unlock_session
    的其它副作用(策略恢复/解冻/terminated 恢复)仍每个 tick 无条件执行。
    再入场冷却 clear_state 已改为仅在 paused→running 转换时执行（2026-07-31），
    避免每 tick 抹掉止损冷却导致 17 秒同向再开。
    """
    # 优先用稳定的 session_id 字符串做 key(id(session) 对每 tick 新查出的
    # ORM 对象无效);都没有时退回 id(session) 保证不抛错。
    _sid = getattr(session, "session_id", None)
    if _sid is None:
        _sid = id(session)
    _prev = _prev_loss_lock_status_map.get(_sid)
    _cur = getattr(session, "status", None)
    _transitioned = (_prev in ("paused", "locked", "loss_locked")) and (_cur == "running")
    _prev_loss_lock_status_map[_sid] = _cur
    return _transitioned


@dataclass
class PaperSessionHost:
    defensive_entered_at: Dict[str, float] = field(default_factory=dict)
    recovery_until: Dict[str, float] = field(default_factory=dict)
    symbol_frozen_set: Dict[str, set] = field(default_factory=dict)
    symbol_frozen_tiers: Dict[str, Dict[str, set]] = field(default_factory=dict)
    strat_pause_meta: Dict[Any, Dict[str, Any]] = field(default_factory=dict)

    paper_loss_locks_disabled: Callable = field(repr=False, default=lambda *a, **k: False)
    clear_strategy_pause_meta: Callable = field(repr=False, default=lambda *a, **k: None)
    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    invalidate_session_status_cache: Callable = field(repr=False, default=lambda *a, **k: None)
    should_log_pause_event: Callable = field(repr=False, default=lambda *a, **k: True)
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    record_strategy_pause: Callable = field(repr=False, default=lambda *a, **k: None)
    safe_commit: Callable = field(repr=False, default=lambda *a, **k: True)
    utc_iso: Callable = field(repr=False, default=lambda *a, **k: None)


def build_paper_session_host(svc) -> PaperSessionHost:
    return PaperSessionHost(
        defensive_entered_at=svc._defensive_entered_at,
        recovery_until=svc._recovery_until,
        symbol_frozen_set=getattr(svc, "_symbol_frozen_set", None) or {},
        symbol_frozen_tiers=getattr(svc, "_symbol_frozen_tiers", None) or {},
        strat_pause_meta=getattr(svc, "_strat_pause_meta", None) or {},
        paper_loss_locks_disabled=svc._paper_loss_locks_disabled,
        clear_strategy_pause_meta=svc._clear_strategy_pause_meta,
        get_trading_account_id=svc._get_trading_account_id,
        invalidate_session_status_cache=svc._invalidate_session_status_cache,
        should_log_pause_event=svc._should_log_pause_event,
        append_event=svc._append_event,
        record_strategy_pause=svc._record_strategy_pause,
        safe_commit=svc._safe_commit,
        utc_iso=svc._utc_iso,
    )


def paper_auto_unlock_session(db: Session, session, host: PaperSessionHost) -> bool:
    if not host.paper_loss_locks_disabled(session):
        return False

    from backend.database.models import AIStrategy

    sid = session.session_id
    changed = False
    pause_reason = (getattr(session, "pause_reason", None) or "").strip()

    # [2026-09-03 v3 F1d] 回撤硬闸（drawdown_limit）不属于"可自动解开的亏损锁"：
    # 会话回撤越过 max_total_drawdown_pct 是资本保护的最后一道线，必须人工复核后
    # 通过 resume 接口恢复。这里整段跳过（包括 symbol 解冻与策略复活）。
    if session.status == "paused" and pause_reason == "drawdown_limit":
        return False

    if session.status in ("defensive", "paused"):
        loss_related = (
            session.status == "defensive"
            or pause_reason in ("circuit_breaker", "risk", "")
            or "日亏损" in pause_reason
            or "连亏" in pause_reason
            or "亏损" in pause_reason
        )
        if loss_related and pause_reason != "manual":
            session.status = "running"
            session.pause_reason = None
            host.defensive_entered_at.pop(sid, None)
            host.recovery_until.pop(sid, None)
            changed = True

    frozen = host.symbol_frozen_set.get(sid)
    if frozen:
        host.symbol_frozen_set[sid] = set()
        changed = True
    if host.symbol_frozen_tiers.get(sid):
        host.symbol_frozen_tiers[sid] = {}
        changed = True

    sids = list(session.active_strategy_ids or []) + list(session.terminated_strategy_ids or [])
    seen_ids = set()
    for strat_id in sids:
        seen_ids.add(str(strat_id))
        strat = db.query(AIStrategy).filter(AIStrategy.strategy_id == strat_id).first()
        if not strat or strat.status not in ("paused", "frozen"):
            continue
        meta = host.strat_pause_meta.get(strat.strategy_id, {})
        if not meta:
            meta = host.strat_pause_meta.get(str(strat.strategy_id), {})
        reason = str(meta.get("reason") or "")
        paused_by = str(meta.get("by") or "")
        if paused_by == "manual" or reason.startswith("手动"):
            continue
        strat.status = "active"
        host.clear_strategy_pause_meta(strat.strategy_id)
        changed = True

    session_symbols = list(session.symbols or []) + list(
        getattr(session, "auto_coin_symbols", None) or []
    )
    session_symbols = sorted({str(s).strip().upper() for s in session_symbols if s})

    active_ids = list(session.active_strategy_ids or [])
    terminated_ids = list(session.terminated_strategy_ids or [])

    def _should_skip_revive(strat) -> bool:
        # 2026-06-19: 统一用 SymbolLockRegistry 判断是否该跳过恢复
        from backend.services.symbol_lock_registry import lock_registry
        _sym = (strat.primary_symbol or "").upper()
        _sid = str(strat.strategy_id)
        if lock_registry.should_skip_revive(_sym, _sid):
            return True
        # 向后兼容：旧的 meta/genome 标记也检查
        meta = host.strat_pause_meta.get(str(strat.strategy_id), {})
        if not meta:
            try:
                meta = host.strat_pause_meta.get(strat.strategy_id, {})
            except Exception:
                meta = {}
        reason = str(meta.get("reason") or "")
        paused_by = str(meta.get("by") or "")
        genome = strat.genome if isinstance(strat.genome, dict) else {}
        genome_reason = str(genome.get("pause_reason") or "")
        if paused_by == "manual" or reason.startswith("手动") or genome_reason.startswith("手动"):
            return True
        if genome_reason == "training_rebalance" or reason == "training_rebalance":
            return True
        return False

    # 模拟盘训练：恢复 terminated 策略（移回 active 列表）
    if session_symbols:
        for strat in db.query(AIStrategy).filter(
            AIStrategy.primary_symbol.in_(session_symbols),
            AIStrategy.status == "terminated",
        ).all():
            if _should_skip_revive(strat):
                continue
            strat.status = "active"
            sid_str = str(strat.strategy_id)
            if sid_str not in active_ids:
                active_ids.append(sid_str)
            if sid_str in terminated_ids:
                terminated_ids.remove(sid_str)
            host.clear_strategy_pause_meta(strat.strategy_id)
            changed = True

    if session_symbols:
        for strat in db.query(AIStrategy).filter(
            AIStrategy.primary_symbol.in_(session_symbols),
            AIStrategy.status.in_(["paused", "frozen"]),
        ).all():
            if str(strat.strategy_id) in seen_ids:
                if _should_skip_revive(strat):
                    continue
            elif _should_skip_revive(strat):
                continue
            strat.status = "active"
            sid_str = str(strat.strategy_id)
            if sid_str not in active_ids:
                active_ids.append(sid_str)
            host.clear_strategy_pause_meta(strat.strategy_id)
            changed = True

    session.active_strategy_ids = active_ids
    session.terminated_strategy_ids = terminated_ids

    if cap_paper_active_strategies(db, session, active_ids, host):
        changed = True
        session.active_strategy_ids = active_ids

    # paused→running 转换只判定一次，供冷却清理 + 心态重置共用
    _resume_transition = _should_reset_loss_protection(session)

    try:
        acct = host.get_trading_account_id(db, session)
        # [2026-07-31 止血] 禁止每个 tick clear_state！
        # 旧逻辑无条件清空全部 symbol 再入场冷却 → 止损 4h 冷却被 coordinator
        # 下一 tick（~30s）抹掉；实测 HYPE 15:54 SL → 15:55 同向再开（17秒）。
        if _resume_transition:
            from backend.services.reentry_cooldown import clear_state
            for sym in session_symbols:
                clear_state(acct, sym)
            logger.info(
                "[FullAuto] session=%s paused→running，已清理再入场冷却 symbols=%s",
                getattr(session, "session_id", "?"),
                session_symbols[:12],
            )
    except Exception as exc:
        logger.debug(f"[FullAuto] paper unlock reentry cooldown skip: {exc}")

    try:
        acct = host.get_trading_account_id(db, session)
        from backend.services.position_memory_manager import reset_loss_protection_state
        # 根因1止血:仅 paused→running 真实转换时重置心态状态机
        if _resume_transition:
            if reset_loss_protection_state(db, acct):
                changed = True
    except Exception as exc:
        logger.debug(f"[FullAuto] paper unlock mental state skip: {exc}")

    try:
        from backend.services.multi_timeframe_orchestrator import mt_orchestrator
        for sym in session_symbols or (session.symbols or []):
            mt_orchestrator._freeze_until.pop(sym, None)
            mt_orchestrator._freeze_reason.pop(sym, None)
    except Exception:
        pass

    if changed:
        session.active_strategy_ids = active_ids
        host.invalidate_session_status_cache(sid)
        # 2026-06-19: paper 模式下锁仓已被 PAPER_DISABLE_LOSS_LOCKS 全局禁用，
        # 恢复 paused 策略是正常操作（不是"解除锁仓"），不再写 paper_unlock 事件。
        # 这个事件导致前端反复弹"模拟盘已自动解除亏损锁仓"提示，是用户反馈的痛点。
        # if host.should_log_pause_event(sid, "paper_unlock"):
        #     host.append_event(
        #         session,
        #         "paper_unlock",
        #         "📗 模拟盘已自动解除亏损锁仓，继续训练开仓",
        #     )
        logger.info(f"[FullAuto] 模拟盘策略恢复（非锁仓解除）{sid}")
    return changed

def cap_paper_active_strategies(
    db: Session, session, active_ids: list, host: PaperSessionHost,
    *, max_per_symbol: Optional[int] = None,
) -> bool:
    if not host.paper_loss_locks_disabled(session):
        return False
    try:
        from backend.config.settings import PAPER_MAX_ACTIVE_STRATEGIES_PER_SYMBOL
        cap = int(max_per_symbol or PAPER_MAX_ACTIVE_STRATEGIES_PER_SYMBOL)
    except Exception:
        cap = 5
    cap = max(1, cap)

    from backend.database.models import AIStrategy
    from collections import defaultdict

    session_symbols = list(session.symbols or []) + list(
        getattr(session, "auto_coin_symbols", None) or []
    )
    sym_set = {str(s).strip().upper() for s in session_symbols if s}
    if not sym_set or not active_ids:
        return False

    strats = db.query(AIStrategy).filter(
        AIStrategy.strategy_id.in_(active_ids),
        AIStrategy.primary_symbol.in_(sym_set),
        AIStrategy.status == "active",
    ).all()
    by_sym: Dict[str, list] = defaultdict(list)
    for st in strats:
        by_sym[str(st.primary_symbol or "").upper()].append(st)

    changed = False
    for sym, group in by_sym.items():
        if len(group) <= cap:
            continue
        group.sort(key=lambda s: int(getattr(s, "id", 0) or 0), reverse=True)
        # [调研轮17 2026-09-16] **不得暂停某 (symbol, tier) 的最后一条 active**。
        # 原实现按 id 倒序在 cap 之外一律暂停；若某层的策略恰好都排在 cap 之外，
        # 该 (币, 层) 就变成零 active ⇒ 提案静默死于 `eval_false:no_active_strategy`
        # （实测 09-16 23:11 `paper cap SOL: kept 5 active, paused 2` 之后
        #  acct14 的 SOL **中线**零 active，而 SOL 中线正是当时在跑的车道）。
        _tier_alive: Dict[str, int] = defaultdict(int)
        for st in group:
            _tier_alive[str(getattr(st, "timeframe_tier", None) or "mid").lower()] += 1
        for st in group[cap:]:
            _st_tier = str(getattr(st, "timeframe_tier", None) or "mid").lower()
            if _tier_alive.get(_st_tier, 0) <= 1:
                logger.warning(
                    "[FullAuto] paper cap %s: 跳过暂停 %s（tier=%s 的最后一条 active，"
                    "暂停会让该层静默开不出单）", sym, str(st.strategy_id)[:10], _st_tier,
                )
                continue
            _tier_alive[_st_tier] -= 1
            st.status = "paused"
            sid_str = str(st.strategy_id)
            if sid_str in active_ids:
                active_ids.remove(sid_str)
            host.record_strategy_pause(
                st.strategy_id, f"paper_cap>{cap}", by="paper_cap"
            )
            # 2026-06-19: 统一注册到 SymbolLockRegistry
            try:
                from backend.services.symbol_lock_registry import lock_registry
                lock_registry.lock(sym, strategy_id=sid_str,
                                   reason_code="session_paused", by="paper_cap",
                                   duration_sec=300)  # 5min 短冷却（cap 是临时的）
            except Exception:
                pass
            changed = True
        logger.info(
            "[FullAuto] paper cap %s: kept %d active, paused %d",
            sym, cap, len(group) - cap,
        )
    return changed

def get_trade_history(db, session, host: PaperSessionHost) -> list:
    try:
        from backend.database.models import PaperPosition
        trading_acct = host.get_trading_account_id(db, session)
        if not trading_acct:
            return []
        # 已平仓记录（最近30条）
        closed = (
            db.query(PaperPosition)
            .filter(PaperPosition.account_id == trading_acct, PaperPosition.status == 'closed')
            .order_by(PaperPosition.closed_at.desc())
            .limit(30)
            .all()
        )
        trades = []
        for p in closed:
            pnl = float(p.partial_realized_pnl or 0)
            trades.append({
                "id": p.id,
                "symbol": p.symbol,
                "side": p.side,
                "status": "closed",
                "entry_price": float(p.entry_price or 0),
                "close_price": float(p.close_price or 0),
                "size": float(p.original_size or p.size or 0),
                "leverage": float(p.leverage or 1),
                "pnl": pnl,
                "close_reason": p.close_reason or "",
                "timeframe_tier": p.timeframe_tier or "",
                "trade_nature": p.trade_nature or "",
                "opened_at": host.utc_iso(p.opened_at),
                "closed_at": host.utc_iso(p.closed_at),
            })
        # 当前持仓（最近16条）
        open_pos = (
            db.query(PaperPosition)
            .filter(PaperPosition.account_id == trading_acct, PaperPosition.status == 'open')
            .order_by(PaperPosition.opened_at.desc())
            .limit(16)
            .all()
        )
        for p in open_pos:
            upnl = float(p.unrealized_pnl or 0)
            trades.append({
                "id": p.id,
                "symbol": p.symbol,
                "side": p.side,
                "status": "open",
                "entry_price": float(p.entry_price or 0),
                "close_price": None,
                "size": float(p.size or 0),
                "leverage": float(p.leverage or 1),
                "pnl": upnl,
                "close_reason": "",
                "timeframe_tier": p.timeframe_tier or "",
                "trade_nature": p.trade_nature or "",
                "opened_at": host.utc_iso(p.opened_at),
                "closed_at": None,
            })
        return trades
    except Exception:
        # [fix] rollback 避免 InFailedSqlTransaction 污染调用方 session
        try:
            db.rollback()
        except Exception:
            pass
        return []

    # ── 整改项2: 模式切换缓冲 ────────────────────────────

def cleanup_duplicate_strategies(db, host: PaperSessionHost):
    from backend.database.models import AIStrategy, FullAutoSession
    from collections import defaultdict
    from datetime import datetime, timedelta, timezone

    try:
        # ── 第一步：清理 active/paused 重复 ──
        all_active = db.query(AIStrategy).filter(
            AIStrategy.status.in_(["active", "paused"]),
        ).order_by(AIStrategy.created_at.asc()).all()

        groups = defaultdict(list)
        for s in all_active:
            genome = s.genome if isinstance(s.genome, dict) else {}
            if genome.get("source") == "hermes_genesis":
                continue
            key = f"{s.account_id}:{s.primary_symbol}:{s.timeframe_tier or 'mid'}"
            groups[key].append(s)

        archived_ids = []
        for key, strats in groups.items():
            if len(strats) <= 1:
                continue
            # [调研轮17 2026-09-16] **keeper 必须优先选 active**。
            # 原实现盲取 `strats[0]`（created_at 最早的一条）；若那条恰好是 paused，
            # 就会把同组所有 active 全部归档 ⇒ 该 (账户, 币, 层) 变成**零 active**，
            # 该 symbol 的提案随即以 `eval_false:no_active_strategy` 静默死掉
            # （实测 09-16 22:26 去重后 acct188 UNI/XRP 各只剩 1 条 paused、其余 archived，
            #  24h 内 UNI 54 行 / XRP 10 行因此被丢弃）。
            # 排序键：active 优先，其次 created_at 升序（同状态内仍保留最早的一条）。
            strats.sort(key=lambda s: (
                0 if str(s.status or "").lower() == "active" else 1,
                str(getattr(s, "created_at", "") or ""),
            ))
            keeper = strats[0]
            for dup in strats[1:]:
                dup.status = "archived"
                archived_ids.append(dup.strategy_id)
                logger.warning(
                    f"[FullAuto] 启动去重: 归档 {dup.strategy_id[:10]}"
                    f"({dup.primary_symbol}/{dup.timeframe_tier})，"
                    f"保留 {keeper.strategy_id[:10]}(status={keeper.status})"
                )

        # ── [调研轮17] 零 active 检测：会话的交易 symbol 在其层必须有 ≥1 条 active ──
        # 去重/健康暂停/生命周期暂停都可能把某个 (币, 层) 的 active 清空，而症状只是
        # 审计里一行 `no_active_strategy` —— 静默且难发现。这里在每次去重后显式列出，
        # 让"某币静默开不出单"在日志里可见（只告警，不改状态：paused 可能是健康判定）。
        try:
            _running = db.query(FullAutoSession).filter(
                FullAutoSession.status.in_(["running", "defensive"])
            ).all()
            _gaps = []
            for _s in _running:
                _acct = (getattr(_s, "paper_account_id", None)
                         or getattr(_s, "account_id", None))
                _syms = {str(x).upper() for x in (list(_s.symbols or [])
                                                  + list(getattr(_s, "auto_coin_symbols", None) or [])) if x}
                if not _acct or not _syms:
                    continue
                _rows = db.query(AIStrategy).filter(
                    AIStrategy.account_id == _acct,
                    AIStrategy.status == "active",
                ).all()
                _have = {(str(r.primary_symbol or "").upper(),
                          str(r.timeframe_tier or "mid").lower()) for r in _rows}
                for _sym in sorted(_syms):
                    _miss = [t for t in ("short", "mid", "long") if (_sym, t) not in _have]
                    if len(_miss) == 3:
                        _gaps.append(f"{_s.session_id}:{_sym}")
            if _gaps:
                logger.warning(
                    "[FullAuto] ⚠️ 零 active 策略的会话 symbol（该币在该层开不出单，"
                    "审计会记 no_active_strategy）: %s", _gaps[:12],
                )
        except Exception as _gap_err:
            logger.debug(f"[FullAuto] 零 active 检测跳过: {_gap_err}")


        if archived_ids:
            sessions = db.query(FullAutoSession).filter(
                FullAutoSession.status.in_(["running", "defensive"])
            ).all()
            for session in sessions:
                active = list(session.active_strategy_ids or [])
                active = [sid for sid in active if sid not in archived_ids]
                session.active_strategy_ids = active
            host.safe_commit(db, "startup_dedup")
            logger.info(f"[FullAuto] 启动去重完成: 归档 {len(archived_ids)} 个重复策略")
        else:
            logger.info("[FullAuto] 启动去重检查: 无重复策略")

        # ── 第二步：清理过期 archived 策略（超过 N 天的删除，默认1天）──
        import os
        _cleanup_days = int(os.environ.get('ARCHIVED_CLEANUP_DAYS', '1'))
        cutoff = datetime.now(timezone.utc) - timedelta(days=_cleanup_days)
        old_archived = db.query(AIStrategy).filter(
            AIStrategy.status == "archived",
            AIStrategy.auto_mode == "full_auto",
            AIStrategy.updated_at < cutoff,
        ).all()
        if old_archived:
            # 从 session 的 terminated_strategy_ids 中也移除
            all_sessions = db.query(FullAutoSession).all()
            old_sids = {s.strategy_id for s in old_archived}
            for s in old_archived:
                db.delete(s)
            for session in all_sessions:
                term = list(session.terminated_strategy_ids or [])
                new_term = [sid for sid in term if sid not in old_sids]
                if len(new_term) != len(term):
                    session.terminated_strategy_ids = new_term
            host.safe_commit(db, "cleanup_archived")
            logger.info(f"[FullAuto] 清理过期archived策略: 删除 {len(old_archived)} 条（>{_cleanup_days}天）")
        else:
            logger.info("[FullAuto] 无过期archived策略需要清理")

    except Exception as e:
        logger.warning(f"[FullAuto] 启动去重失败: {e}")
        try:
            db.rollback()
        except Exception:
            pass

    # ══════════════════════════════════════════════════
    #  V3 因子管道（DB 批量落库，避免 database is locked）
    # ══════════════════════════════════════════════════
