"""编排器后台 / 缓存清理 / 调度桩 — 从 monolith 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class OrchBackgroundHost:
    last_cache_purge: float = 0.0
    last_close_time: Dict[str, float] = field(default_factory=dict)
    last_reduce_time: Dict[str, Any] = field(default_factory=dict)
    master_strat_cache: Dict[str, Any] = field(default_factory=dict)
    partial_close_tracker: Dict[str, dict] = field(default_factory=dict)
    market_scan_cache: Dict[str, dict] = field(default_factory=dict)
    market_scan_cache_ts: float = 0.0
    strategy_creation_ts: Dict[str, float] = field(default_factory=dict)
    health_status: Dict[str, Any] = field(default_factory=dict)
    current_ai_tiers: Optional[List[str]] = None
    last_orch_decisions: Dict[str, Any] = field(default_factory=dict)
    last_orch_decisions_ts: float = 0.0
    orch_bg_thread: Any = None
    orch_bg_session_id: Optional[str] = None
    orch_bg_symbols: List[str] = field(default_factory=list)
    orch_bg_running: bool = False
    last_unified_snapshot: Any = None
    owner: Any = field(default=None, repr=False)

    ensure_fresh_orch_decisions: Callable = field(repr=False, default=lambda *a, **k: {})
    tier_confidence_pct: Callable = field(repr=False, default=lambda *a, **k: 50)
    resolve_session_trade_symbols: Callable = field(repr=False, default=lambda *a, **k: [])
    active_exchange: Callable = field(repr=False, default=lambda: "binance")
    orch_payload_from_decision: Callable = field(repr=False, default=lambda *a, **k: {})


def _sync_orch_bg_to_owner(host: OrchBackgroundHost) -> None:
    """后台线程会持续改写 host 字段，需同步回 svc。"""
    owner = host.owner
    if owner is None:
        return
    owner._orch_bg_thread = host.orch_bg_thread
    owner._orch_bg_session_id = host.orch_bg_session_id
    owner._orch_bg_symbols = host.orch_bg_symbols
    owner._orch_bg_running = host.orch_bg_running
    owner._market_scan_cache = host.market_scan_cache
    owner._market_scan_cache_ts = host.market_scan_cache_ts
    owner._last_orch_decisions = host.last_orch_decisions
    owner._last_orch_decisions_ts = host.last_orch_decisions_ts
    owner._last_unified_snapshot = host.last_unified_snapshot


def build_orch_background_host(svc) -> OrchBackgroundHost:
    return OrchBackgroundHost(
        last_cache_purge=float(getattr(svc, "_last_cache_purge", 0) or 0),
        last_close_time=getattr(svc, "_last_close_time", None) or {},
        last_reduce_time=getattr(svc, "_last_reduce_time", None) or {},
        master_strat_cache=getattr(svc, "_master_strat_cache", None) or {},
        partial_close_tracker=getattr(svc, "_partial_close_tracker", None) or {},
        market_scan_cache=getattr(svc, "_market_scan_cache", None) or {},
        market_scan_cache_ts=float(getattr(svc, "_market_scan_cache_ts", 0) or 0),
        strategy_creation_ts=getattr(svc, "_strategy_creation_ts", None) or {},
        health_status=getattr(svc, "_health_status", None) or {},
        current_ai_tiers=getattr(svc, "_current_ai_tiers", None),
        last_orch_decisions=getattr(svc, "_last_orch_decisions", None) or {},
        last_orch_decisions_ts=float(getattr(svc, "_last_orch_decisions_ts", 0) or 0),
        orch_bg_thread=getattr(svc, "_orch_bg_thread", None),
        orch_bg_session_id=getattr(svc, "_orch_bg_session_id", None),
        orch_bg_symbols=list(getattr(svc, "_orch_bg_symbols", None) or []),
        orch_bg_running=bool(getattr(svc, "_orch_bg_running", False)),
        last_unified_snapshot=getattr(svc, "_last_unified_snapshot", None),
        owner=svc,
        ensure_fresh_orch_decisions=svc._ensure_fresh_orch_decisions,
        tier_confidence_pct=svc._tier_confidence_pct,
        resolve_session_trade_symbols=svc._resolve_session_trade_symbols,
        active_exchange=svc._active_exchange,
        orch_payload_from_decision=svc._orch_payload_from_decision,
    )


def build_fast_stability_result(
    symbols,
    *,
    trigger: str = "timeout",
    timeout_s: Optional[float] = None,
) -> dict:
    from datetime import datetime, timezone

    sym_list = [str(s).upper() for s in (symbols or []) if str(s).strip()]
    if trigger == "forced":
        reasoning = (
            "快速稳定模式：深度分析链路被降级为后台能力，本轮先基于已有持仓/"
            "行情缓存保守观望，避免主循环卡住导致决策日志不更新。"
        )
        overall = "快速稳定模式：主循环保持运行，保守观望"
        summary = "深度分析链路降级，主循环优先保持可运行和可观察"
        analyst_label = "快速稳定决策"
        confidence = 45
    else:
        ts_label = f"{timeout_s:.0f}s" if timeout_s else "预算"
        reasoning = (
            f"深度分析超时({ts_label})或未完成：降级为保守 hold，"
            f"基于已有持仓/行情缓存观望，等待下一轮重新分析。"
        )
        overall = "深度分析超时降级：主循环继续运行，保守观望"
        summary = f"LLM 分析超时({ts_label})，降级 hold"
        analyst_label = "超时降级决策"
        confidence = 35

    decisions = []
    for sym in sym_list:
        decisions.append({
            "symbol": sym,
            "action": "hold",
            "confidence": confidence,
            "reasoning": reasoning,
            "trade_nature": "swing",
            "timeframe_tier": "mid",
            "_orch_scheduled": True,
        })
        decisions.append({
            "symbol": sym,
            "action": "hold",
            "confidence": confidence,
            "reasoning": reasoning + " [long tier stub]",
            "trade_nature": "trend_follow",
            "timeframe_tier": "long",
            "_orch_scheduled": True,
        })
    return {
        "reports": {
            "fast_stability": {
                "analyst": analyst_label,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "risk_score": 50,
                "summary": summary,
                "signals": [],
                "recommendation": "保守 hold，等待下一轮或后台深度信号",
            }
        },
        "master_decision": {
            "overall_assessment": overall,
            "risk_level": "medium",
            "decisions": decisions,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "_analysis_degraded": True,
    }

def purge_stale_caches(host: OrchBackgroundHost) -> None:
    now = time.time()
    if now - host.last_cache_purge < 300:
        return
    host.last_cache_purge = now
    purged = 0

    # 清理已过期的平仓冷却（reentry_cooldown 自管理，F1-5）
    try:
        from backend.services.reentry_cooldown import purge_expired
        purged += purge_expired()
    except Exception:
        pass
    # 清理过期的 _last_close_time（超过 2 小时的）
    stale_close = [k for k, v in host.last_close_time.items()
                   if now - v > 7200]
    for k in stale_close:
        del host.last_close_time[k]
    purged += len(stale_close)

    # 清理过期的 _last_reduce_time（超过 2 小时的）
    stale_reduce = [k for k, v in host.last_reduce_time.items()
                    if hasattr(v, 'timestamp') and now - v.timestamp() > 7200]
    for k in stale_reduce:
        del host.last_reduce_time[k]
    purged += len(stale_reduce)

    # 限制 _master_strat_cache 大小（最多 200 条）
    if len(getattr(host, 'master_strat_cache', {})) > 200:
        host.master_strat_cache.clear()
        purged += 1

    # 清理过期的 _partial_close_tracker（reset_at 超过 4 小时的）
    stale_partial = [k for k, v in host.partial_close_tracker.items()
                     if now - v.get("reset_at", 0) > 14400]
    for k in stale_partial:
        del host.partial_close_tracker[k]
    purged += len(stale_partial)

    # 限制 _market_scan_cache 大小
    if len(host.market_scan_cache) > 100:
        host.market_scan_cache.clear()
        host.market_scan_cache_ts = 0
        purged += 1

    # 清理过期的 _strategy_creation_ts（超过 2 小时的）
    stale_creation = [k for k, v in host.strategy_creation_ts.items()
                      if now - v > 7200]
    for k in stale_creation:
        del host.strategy_creation_ts[k]
    purged += len(stale_creation)

    # 清理 _health_status 的队列（保留最近 20 条）
    _issues_keys = ["data_issues", "ai_issues", "rejected_decisions"]
    for ik in _issues_keys:
        lst = host.health_status.get(ik, [])
        if len(lst) > 20:
            host.health_status[ik] = lst[-20:]
            purged += len(lst) - 20

    # 清理 reentry_cooldown 模块级缓存
    try:
        from backend.services.reentry_cooldown import purge_expired
        purged += purge_expired()
    except Exception:
        pass

    if purged > 0:
        logger.info(f"[FullAuto] 缓存清理: 清除 {purged} 个过期条目")

    # 无论是否清理了缓存，都执行 GC 以防止内存泄漏
    import gc
    gc.collect()

def inject_orch_scheduled_stubs(
    decisions: list,
    market_summary: dict,
    host: OrchBackgroundHost,
    session=None,
) -> list:
    from backend.config.settings import MIDLONG_AI_MANDATORY, FULLAUTO_AI_DOMINANT

    decisions = list(decisions or [])
    _orch_decs = host.ensure_fresh_orch_decisions(market_summary)
    if not _orch_decs:
        try:
            from backend.services.multi_timeframe_orchestrator import mt_orchestrator as _mto2
            _symbols_for_orch = list((market_summary or {}).keys())
            if MIDLONG_AI_MANDATORY and session is not None:
                _symbols_for_orch = list(
                    dict.fromkeys(list(getattr(session, "symbols", []) or []) + _symbols_for_orch)
                )
            elif _symbols_for_orch:
                _symbols_for_orch = _symbols_for_orch[:6]
            if _symbols_for_orch:
                _orch_decs = _mto2.evaluate_portfolio(_symbols_for_orch)
                host.last_orch_decisions = _orch_decs
                host.last_orch_decisions_ts = time.time()
                logger.info(
                    "[Fix18] 即时 evaluate orchestrator: %s symbols",
                    len(_orch_decs),
                )
        except Exception as _orch_err:
            logger.warning("[Fix18] 即时 orchestrator 评估失败: %s", _orch_err)

    _symbols_to_walk = [str(k).upper() for k in (_orch_decs or {}).keys()]
    if MIDLONG_AI_MANDATORY and session is not None:
        _symbols_to_walk = list(
            dict.fromkeys(
                [str(s).upper() for s in (getattr(session, "symbols", None) or [])]
                + _symbols_to_walk
            )
        )

    _active_tiers = getattr(host, "current_ai_tiers", None) or ["mid", "long"]

    for _r_sym in _symbols_to_walk:
        _r_dec = (_orch_decs or {}).get(_r_sym) or (_orch_decs or {}).get(_r_sym.upper())
        _slots = getattr(_r_dec, "recommended_slots", []) or [] if _r_dec else []
        _actions = getattr(_r_dec, "slot_actions", {}) or {} if _r_dec else {}
        if MIDLONG_AI_MANDATORY:
            _slots = list(dict.fromkeys(list(_slots) + ["mid", "long"]))
        if _slots:
            logger.info("[Fix18] %s slots=%s actions=%s", _r_sym, _slots, _actions)

        _mid_ok = (
            "mid" in _active_tiers
            and "mid" in _slots
            and (MIDLONG_AI_MANDATORY or _actions.get("mid") == "create")
        )
        # MidLong v2：独立调度开启时不再注入 mid/long「调度桩」假决策（conf 常为 0，
        # 污染编排器与前端日志；真实分析由 midlong 独立循环完成）。
        _skip_midlong_stubs = False
        try:
            from backend.config.settings import MIDLONG_AGENT_INDEPENDENT_SCHEDULER
            _skip_midlong_stubs = bool(MIDLONG_AGENT_INDEPENDENT_SCHEDULER)
        except Exception:
            _skip_midlong_stubs = True
        if _mid_ok and not _skip_midlong_stubs:
            _has_swing = any(
                d.get("symbol", "").upper() == _r_sym.upper()
                and (d.get("trade_nature", "") or "").lower() == "swing"
                for d in decisions
            )
            if not _has_swing:
                _mid_conf = host.tier_confidence_pct(tier="mid", orch_dec=_r_dec)
                decisions.append({
                    "symbol": _r_sym,
                    "action": "hold",
                    "confidence": _mid_conf,
                    "trade_nature": "swing",
                    "timeframe_tier": "mid",
                    "reasoning": (
                        "[中长线AI强制→SwingAgent LLM]"
                        if MIDLONG_AI_MANDATORY
                        else "[总控独立调度→SwingAgent待分析]"
                    ),
                    "_orch_scheduled": True,
                    "_orch_slot_action": "create" if FULLAUTO_AI_DOMINANT else "create",
                })
                logger.info("[Fix18] %s mid tier → SwingAgent 调度桩 conf=%s", _r_sym, _mid_conf)
        elif _mid_ok and _skip_midlong_stubs:
            logger.debug("[MidLong] skip mid stub %s (independent scheduler)", _r_sym)

        # [2026-07-21 修复] 之前的修复在 master_execution.py / mlto_cycle.py 加了跳过，
        # 但本函数（orch_background）是长线决策桩的另一个入口，漏了过滤，导致 AI 选币
        # （KBONK/HYPE/PUMP/VVV/ONDO）仍被标记为 [中长线AI强制→TrendAgent LLM]。
        # 且原判断读 session.auto_coin_symbols 这个可能过期的 ORM 快照——AI选币每
        # ~30min动态轮换，本函数所在的 tick 若持有 session 较久，快照会滞后于真实DB
        # 状态。改为调用统一的正向白名单函数（每次现查DB），与 mlto_cycle.py /
        # tier_fanout.py 用同一份判断逻辑，不再各自维护一份可能不同步的排除集合。
        # 默认按"非固定币"处理（宁可漏做一次长线分析，也不让AI选币混进长线）——
        # 查询失败/session 缺失时保持这个保守默认，不回退到"放行"。
        _sym_is_auto_coin = True
        try:
            if session is not None:
                from backend.services.auto_coin_selector import get_fixed_symbols_for_session
                _sid = getattr(session, "session_id", None)
                if _sid:
                    _sym_is_auto_coin = _r_sym.upper() not in get_fixed_symbols_for_session(_sid)
        except Exception:
            pass

        _long_ok = (
            "long" in _active_tiers
            and "long" in _slots
            and not _sym_is_auto_coin
            and (MIDLONG_AI_MANDATORY or _actions.get("long") == "create")
        )
        if _long_ok and not _skip_midlong_stubs:
            _has_trend = any(
                d.get("symbol", "").upper() == _r_sym.upper()
                and (d.get("trade_nature", "") or "").lower() in ("trend_follow", "position")
                for d in decisions
            )
            if not _has_trend:
                _long_conf = host.tier_confidence_pct(tier="long", orch_dec=_r_dec)
                decisions.append({
                    "symbol": _r_sym,
                    "action": "hold",
                    "confidence": _long_conf,
                    "trade_nature": "trend_follow",
                    "timeframe_tier": "long",
                    "reasoning": (
                        "[中长线AI强制→TrendAgent LLM]"
                        if MIDLONG_AI_MANDATORY
                        else "[总控独立调度→TrendAgent待分析]"
                    ),
                    "_orch_scheduled": True,
                    "_orch_slot_action": "create" if FULLAUTO_AI_DOMINANT else "create",
                })
                logger.info("[Fix18] %s long tier → TrendAgent 调度桩 conf=%s", _r_sym, _long_conf)
        elif _long_ok and _skip_midlong_stubs:
            logger.debug("[MidLong] skip long stub %s (independent scheduler)", _r_sym)
    return decisions

def ensure_orchestrator_bg_running(
    session_id: str, symbols: list, host: OrchBackgroundHost,
) -> None:
    if host.orch_bg_thread is not None and host.orch_bg_thread.is_alive():
        # 已在跑：更新 session_id，下轮循环会从 DB 刷新完整 symbol 列表
        host.orch_bg_session_id = session_id
        if symbols:
            host.orch_bg_symbols = list(symbols)
        _sync_orch_bg_to_owner(host)
        return

    host.orch_bg_session_id = session_id
    host.orch_bg_symbols = list(symbols or [])
    host.orch_bg_running = True

    def _orch_bg_loop():
        """编排器后台循环：逐个评估币种，评估完一个立即写入缓存"""
        logger.info(
            f"[FullAuto][OrchBG] 编排器后台线程启动, session={session_id}"
        )
        while host.orch_bg_running:
            try:
                _syms = list(host.orch_bg_symbols or symbols or [])
                _sid = getattr(host, "orch_bg_session_id", None) or session_id
                if _sid:
                    try:
                        from backend.database.connection import SessionLocal
                        from backend.database.models import FullAutoSession
                        _db = SessionLocal()
                        try:
                            _sess = _db.query(FullAutoSession).filter(
                                FullAutoSession.session_id == _sid
                            ).first()
                            if _sess:
                                _syms = host.resolve_session_trade_symbols(_sess, _db)
                                host.orch_bg_symbols = _syms
                                _sync_orch_bg_to_owner(host)
                        finally:
                            _db.close()
                    except Exception:
                        pass
                if not _syms:
                    _syms = list(symbols or ["BTC"])
                _snap = host.last_unified_snapshot
                if _snap is None:
                    try:
                        from backend.services.unified_data_pool import unified_data_pool
                        _snap = unified_data_pool.capture_snapshot(
                            symbols=_syms,
                            account_id=None,
                            environment=host.active_exchange(),
                            include_klines=True,
                            include_strategy=False,
                            light_mode=True,
                        )
                    except Exception:
                        pass
                if _snap:
                    from backend.services.multi_timeframe_orchestrator import mt_orchestrator
                    _completed = 0
                    _bg_orch_decs = {}
                    for _sym in _syms:
                        if not host.orch_bg_running:
                            break
                        try:
                            dec = mt_orchestrator.evaluate(_sym, _snap)
                            orch_data = host.orch_payload_from_decision(dec)
                            host.market_scan_cache.setdefault(_sym, {}).update({
                                "orchestrator": orch_data,
                                "recommended_nature": dec.recommended_nature,
                            })
                            _bg_orch_decs[_sym] = dec
                            try:
                                from backend.services.scalp.scalp_advisory_cache import (
                                    scalp_advisory_cache,
                                )
                                from backend.services.scalp.scalp_structure_scanner import (
                                    scalp_structure_scanner,
                                )
                                scalp_advisory_cache.merge_orchestrator(_sym, orch_data)
                                _mkt_slice = dict(host.market_scan_cache.get(_sym) or {})
                                _adv = scalp_structure_scanner.scan(_sym, _mkt_slice, orch_data)
                                host.market_scan_cache.setdefault(_sym, {})[
                                    "scalp_advisory"
                                ] = _adv.to_dict()
                            except Exception as _adv_err:
                                logger.debug(f"[FullAuto][OrchBG] advisory {_sym}: {_adv_err}")
                            _completed += 1
                        except Exception as _sym_err:
                            logger.debug(f"[FullAuto][OrchBG] {_sym} 评估失败: {_sym_err}")
                    if _bg_orch_decs:
                        from backend.config.settings import MIDLONG_ORCH_SNAPSHOT_V2
                        if MIDLONG_ORCH_SNAPSHOT_V2:
                            host.last_orch_decisions = _bg_orch_decs
                            host.last_orch_decisions_ts = time.time()
                    host.market_scan_cache_ts = time.time()
                    _sync_orch_bg_to_owner(host)
                    logger.info(
                        f"[FullAuto][OrchBG] 编排器评估完成: "
                        f"{_completed}/{len(_syms)} 个币种 symbols={_syms}"
                    )
                else:
                    logger.debug("[FullAuto][OrchBG] 无快照，跳过本轮评估")
            except Exception as _e:
                logger.warning(f"[FullAuto][OrchBG] 评估异常: {_e}")

            # 等 ORCH_BG_INTERVAL_SEC（每 30s 检查一次是否应该退出）
            from backend.config.settings import ORCH_BG_INTERVAL_SEC
            _sleep_chunks = max(1, ORCH_BG_INTERVAL_SEC // 30)
            for _ in range(_sleep_chunks):
                if not host.orch_bg_running:
                    return
                time.sleep(30)

    host.orch_bg_thread = threading.Thread(
        target=_orch_bg_loop,
        daemon=True,
        name="orchestrator-bg",
    )
    host.orch_bg_thread.start()
    _sync_orch_bg_to_owner(host)

    # ═══════════════════════════════════════════════════════════════════
    #  QAA v3.0 TickOrchestrator 驱动的完整交易 tick
    # ═══════════════════════════════════════════════════════════════════
