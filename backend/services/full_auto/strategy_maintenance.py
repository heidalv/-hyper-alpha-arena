"""策略维护 — cleanup_stale / merge_duplicate 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class StrategyMaintenanceHost:
    safe_commit: Callable = field(repr=False, default=lambda *a, **k: True)
    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    clear_master_strat_cache: Callable = field(repr=False, default=lambda: None)


def build_strategy_maintenance_host(svc) -> StrategyMaintenanceHost:
    return StrategyMaintenanceHost(
        safe_commit=svc._safe_commit,
        get_trading_account_id=svc._get_trading_account_id,
        clear_master_strat_cache=svc._clear_master_strat_cache,
    )


def cleanup_stale_strategies(db: Session, host: StrategyMaintenanceHost) -> dict:
    from backend.database.models import FullAutoSession, AIStrategy

    stopped_sessions = db.query(FullAutoSession).filter(
        FullAutoSession.status == "stopped"
    ).all()

    total_deleted = 0
    cleaned_sessions = 0

    # [2026-07-11 性能修复] 原逻辑逐 session、逐 sid 各发一次 AIStrategy 查询
    # （M个已停止会话 × N个策略/会话 = M*N 次 round-trip）。这里先收集全部已停止
    # 会话涉及的 sid，一次 IN(...) 批量查出，再按需 delete，逐会话清空字段不变。
    all_sids_by_session: list = []
    union_sids: set = set()
    for session in stopped_sessions:
        all_sids = list(set(
            (session.active_strategy_ids or []) +
            (session.terminated_strategy_ids or [])
        ))
        all_sids_by_session.append((session, all_sids))
        union_sids.update(all_sids)

    strat_map: dict = {}
    if union_sids:
        strats_to_delete = db.query(AIStrategy).filter(
            AIStrategy.strategy_id.in_(union_sids)
        ).all()
        strat_map = {s.strategy_id: s for s in strats_to_delete}

    already_deleted: set = set()
    for session, all_sids in all_sids_by_session:
        if not all_sids:
            continue

        for sid in all_sids:
            if sid in already_deleted:
                continue
            strat = strat_map.get(sid)
            if strat is not None:
                db.delete(strat)
                already_deleted.add(sid)
                total_deleted += 1

        session.active_strategy_ids = []
        session.terminated_strategy_ids = []
        cleaned_sessions += 1

    # 额外清理：查找状态为 paused 但不属于任何运行中会话的"孤儿策略"
    running_sessions = db.query(FullAutoSession).filter(
        FullAutoSession.status.in_(["running", "paused"])
    ).all()
    running_sids = set()
    for s in running_sessions:
        running_sids.update(s.active_strategy_ids or [])
        running_sids.update(s.terminated_strategy_ids or [])

    orphan_strategies = db.query(AIStrategy).filter(
        AIStrategy.status == "paused",
        AIStrategy.auto_mode.isnot(None),
    ).all()
    orphan_deleted = 0
    for strat in orphan_strategies:
        if strat.strategy_id not in running_sids:
            db.delete(strat)
            orphan_deleted += 1

    total_deleted += orphan_deleted
    host.safe_commit(db, "cleanup_stale")

    logger.info(
        f"[FullAuto] 清理完成: {cleaned_sessions} 个已停止会话, "
        f"删除 {total_deleted} 个残留策略 (含 {orphan_deleted} 个孤儿策略)"
    )
    return {
        "success": True,
        "cleaned_sessions": cleaned_sessions,
        "deleted_strategies": total_deleted,
        "orphan_deleted": orphan_deleted,
    }


def merge_duplicate_strategies(db: Session, session_id: str, host: StrategyMaintenanceHost) -> dict:
    from collections import defaultdict
    from backend.database.models import FullAutoSession, AIStrategy, StrategyMemory
    from backend.services.autonomous_strategy_service import autonomous_service

    session = db.query(FullAutoSession).filter(
        FullAutoSession.session_id == session_id
    ).first()
    if not session:
        return {"success": False, "error": "会话不存在"}
    if session.status == "stopped":
        return {"success": False, "error": "会话已停止，无法合并"}

    symbols = session.symbols or []
    norm = lambda x: (x or "").upper().replace("/USDT", "").strip()
    sym_set = {norm(s) for s in symbols if norm(s)}
    if not sym_set:
        return {"success": False, "error": "会话未配置交易对"}

    # P5-fix(2026-05-08): paper 模式合并要查 paper_account 的策略
    account_id = host.get_trading_account_id(db, session)
    cands = db.query(AIStrategy).filter(
        AIStrategy.account_id == account_id,
        AIStrategy.auto_mode == "full_auto",
        AIStrategy.status.in_(["active", "paused"]),
    ).all()

    # 按 symbol:tier 分组（而非仅 symbol），保护多周期分层
    by_key = defaultdict(list)
    for st in cands:
        genome = st.genome if isinstance(st.genome, dict) else {}
        if genome.get("source") == "hermes_genesis":
            continue
        ps = norm(st.primary_symbol)
        if ps in sym_set:
            tier = getattr(st, 'timeframe_tier', None) or 'mid'
            key = f"{ps}:{tier}"
            by_key[key].append(st)

    # [2026-07-11 性能修复] 原 _mem_trades 每次排序都对每个候选策略单独查一次
    # StrategyMemory（sorted 内部比较次数还会放大调用次数）。这里在排序前按
    # cands 的全部 strategy_id 一次性批量查出，_mem_trades 改为查内存字典。
    _mem_trades_map: dict = {}
    if cands:
        _mem_rows = db.query(StrategyMemory).filter(
            StrategyMemory.strategy_id.in_([c.strategy_id for c in cands])
        ).all()
        _mem_trades_map = {m.strategy_id: int(m.total_trades or 0) for m in _mem_rows}

    def _mem_trades(sid: str) -> int:
        return _mem_trades_map.get(sid, 0)

    def _sort_key(st: AIStrategy):
        act = 1 if (st.status or "") == "active" else 0
        tr = _mem_trades(st.strategy_id)
        u = st.updated_at or st.activated_at or st.created_at
        try:
            ts = u.timestamp() if u is not None and hasattr(u, "timestamp") else 0.0
        except Exception:
            ts = 0.0
        return (act, tr, ts)

    active_ids = list(session.active_strategy_ids or [])
    term_ids = list(session.terminated_strategy_ids or [])
    detail = []
    removed_all = []

    for key, rows in by_key.items():
        if len(rows) <= 1:
            continue
        ranked = sorted(rows, key=_sort_key, reverse=True)
        keeper = ranked[0]
        losers = ranked[1:]

        for lo in losers:
            lo.status = "archived"
            try:
                autonomous_service.unregister_strategy(lo.strategy_id)
            except Exception:
                pass
            if lo.strategy_id in active_ids:
                active_ids = [x for x in active_ids if x != lo.strategy_id]
            if lo.strategy_id not in term_ids:
                term_ids.append(lo.strategy_id)
            removed_all.append(lo.strategy_id)

        if keeper.strategy_id not in active_ids:
            active_ids.append(keeper.strategy_id)
        if keeper.strategy_id in term_ids:
            term_ids = [x for x in term_ids if x != keeper.strategy_id]

        keeper.status = "active"
        # 保留 keeper 的 timeframe_tier（不再清除）
        try:
            autonomous_service.register_strategy(keeper.strategy_id)
        except Exception:
            pass

        detail.append({
            "key": key,
            "symbol": key.split(":")[0],
            "tier": key.split(":")[1] if ":" in key else "mid",
            "kept_strategy_id": keeper.strategy_id,
            "removed_strategy_ids": [x.strategy_id for x in losers],
        })

    session.active_strategy_ids = active_ids
    session.terminated_strategy_ids = term_ids
    if not host.safe_commit(db, f"merge_dup_{session_id}"):
        return {"success": False, "error": "数据库提交失败"}

    host.clear_master_strat_cache()

    logger.info(
        f"[FullAuto] 合并重复策略 session={session_id} "
        f"处理 {len(detail)} 个 symbol:tier 组合, 移除 {len(removed_all)} 条实例"
    )
    return {
        "success": True,
        "merged_symbol_count": len(detail),
        "removed_strategy_count": len(removed_all),
        "detail": detail,
    }
