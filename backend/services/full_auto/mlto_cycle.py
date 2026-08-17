"""MLTO 中长线维护与执行 — 从 monolith 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class MltoCycleHost:
    mlto_handled_keys: Set[str] = field(default_factory=set)
    mlto_handled_lock: Any = None
    midlong_persistence_state: Dict[str, Dict] = field(default_factory=dict)
    current_ai_tiers: Optional[List[str]] = None
    last_orch_decisions: Dict[str, Any] = field(default_factory=dict)
    last_orch_decisions_ts: float = 0.0
    # [Phase 5] 与 analyst_system_cycle 共享同一批 StagedTpState，避免分批止盈双套状态
    long_tier_staged_tp_state: Dict[str, Any] = field(default_factory=dict)

    inject_midlong_indicators: Callable = field(repr=False, default=lambda *a, **k: None)
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    format_agent_event_detail: Callable = field(repr=False, default=lambda *a, **k: "")
    try_execute_independent_agent_open: Callable = field(repr=False, default=lambda *a, **k: False)
    persist_independent_scan_log: Callable = field(repr=False, default=lambda *a, **k: None)
    build_midlong_agent_envelope: Callable = field(repr=False, default=lambda *a, **k: {})
    # P0 缺口：execute_midlong_open → try_execute_independent_agent_open 需要这两个
    # 方法；此前缺失导致「open_ready 后 AttributeError，探针永远不成交」。
    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    evaluate_and_execute_proposal: Callable = field(repr=False, default=lambda *a, **k: False)


def build_mlto_cycle_host(svc) -> MltoCycleHost:
    lock = getattr(svc, "_mlto_handled_lock", None)
    if lock is None:
        lock = threading.Lock()
        svc._mlto_handled_lock = lock
    handled = getattr(svc, "_mlto_handled_keys", None)
    if handled is None:
        handled = set()
        svc._mlto_handled_keys = handled
    staged_tp_state = getattr(svc, "_long_tier_staged_tp_state", None)
    if staged_tp_state is None:
        staged_tp_state = {}
        svc._long_tier_staged_tp_state = staged_tp_state
    return MltoCycleHost(
        mlto_handled_keys=handled,
        mlto_handled_lock=lock,
        midlong_persistence_state=svc._midlong_persistence_state,
        current_ai_tiers=getattr(svc, "_current_ai_tiers", None),
        last_orch_decisions=getattr(svc, "_last_orch_decisions", None) or {},
        last_orch_decisions_ts=float(getattr(svc, "_last_orch_decisions_ts", 0) or 0),
        long_tier_staged_tp_state=staged_tp_state,
        inject_midlong_indicators=svc._inject_midlong_indicators,
        append_event=svc._append_event,
        format_agent_event_detail=svc._format_agent_event_detail,
        try_execute_independent_agent_open=svc._try_execute_independent_agent_open,
        persist_independent_scan_log=svc._persist_independent_scan_log,
        build_midlong_agent_envelope=svc._build_midlong_agent_envelope,
        get_trading_account_id=svc._get_trading_account_id,
        evaluate_and_execute_proposal=svc._evaluate_and_execute_proposal,
    )


def _mlto_close_symbol(*, db, session, symbol: str, thesis=None, reason: str = "mlto_invalidation") -> bool:
    """[阶段3e + Phase A] MLTO invalidation close 的统一执行出口。

    复用 paper_engine.close_position（与 _run_midlong_active_exit 同一路径）。

    [Phase A 修复 Bug3] 方向不再从 thesis.direction 推断——thesis 失效后方向可能已
    翻转到持仓的反向，用翻转后的方向调 close_position 会因 side 不匹配返回 None。
    改为：优先查 DB 的 PaperPosition 拿实际持仓 side；只有 DB 查不到时才退回
    thesis.direction 兜底，再不行才双向尝试。

    返回 True 表示至少平掉一个仓位。
    """
    try:
        from backend.services.paper_trading_engine import paper_engine
    except Exception as _pe_err:
        logger.warning("[MLTO] paper_engine 不可用, close 跳过 %s: %s", symbol, _pe_err)
        return False

    acct_id = getattr(session, "paper_account_id", None) or getattr(session, "account_id", None)
    if not acct_id:
        logger.debug("[MLTO] close 跳过 %s: 无 account_id", symbol)
        return False

    sym_u = str(symbol or "").upper()

    # ── [Phase A 修复 Bug3] 主路径：查 DB 的实际 open 仓位 side ──
    # 不从 thesis.direction 推断（thesis 失效后方向可能已翻转，导致 side 不匹配 → 平仓失败）。
    db_side: Optional[str] = None
    if db is not None:
        try:
            from backend.database.models import PaperPosition
            pos = db.query(PaperPosition).filter(
                PaperPosition.account_id == acct_id,
                PaperPosition.symbol == sym_u,
                PaperPosition.status == "open",
            ).first()
            if pos is not None:
                db_side = str(pos.side or "").lower()
        except Exception as _db_err:
            logger.debug("[MLTO] close 查 DB side 失败 %s, 退回 thesis 兜底: %s", sym_u, _db_err)

    closed_any = False
    if db_side in ("long", "short"):
        closed_any = paper_engine.close_position(db, acct_id, sym_u, db_side, reason=reason[:120]) is not None
        return closed_any

    # ── 兜底1：DB 查不到（无持仓或查询失败）→ 用 thesis.direction ──
    direction = str(getattr(thesis, "direction", "") or "").lower()
    if direction == "long":
        closed_any = paper_engine.close_position(db, acct_id, sym_u, "long", reason=reason[:120]) is not None
    elif direction == "short":
        closed_any = paper_engine.close_position(db, acct_id, sym_u, "short", reason=reason[:120]) is not None
    else:
        # 兜底2：方向未知 → 尝试两边（只会平掉实际存在的那一边）
        for _side in ("long", "short"):
            try:
                if paper_engine.close_position(db, acct_id, sym_u, _side, reason=reason[:120]) is not None:
                    closed_any = True
            except Exception:
                pass
    return closed_any


def maintain_mlto_theses_for_session(
    *,
    session,
    market_summary: dict,
    analyst_reports: dict,
    mode: str,
    portfolio: dict,
    host: MltoCycleHost,
    symbols_batch: Optional[List[str]] = None,
    mid_universe: Optional[List[str]] = None,
    run_mid: bool = True,
    run_long: bool = True,
    light_context: bool = False,
) -> None:
    session_id = getattr(session, "session_id", "") or ""
    symbols = list(dict.fromkeys(symbols_batch or getattr(session, "symbols", None) or []))
    if not symbols:
        symbols = list((market_summary or {}).keys())[:16]
    if not symbols:
        return
    _session_status = getattr(session, "status", "running")
    _trade_mode = (mode or getattr(session, "trading_mode", None) or "paper").strip().lower()
    if _trade_mode in ("running", "defensive", "paused"):
        _trade_mode = (getattr(session, "trading_mode", None) or "paper").strip().lower()

    handled = host.mlto_handled_keys
    if not isinstance(handled, set):
        handled = set(handled or [])
        host.mlto_handled_keys = handled
    # 主循环（_execute_master_decisions）与独立 mid/long 循环可能并发调用本方法，
    # 原先"检查 key 是否已处理 → 跑 LLM → 事后 add(key)"是非原子的 check-then-act，
    # 两个线程可能都判定 key 未处理、都跑完整 LLM 分析并各自开一次仓。
    # 用锁把"检查+占位"收敛成原子操作，占位失败（key 已被其他线程占用）直接跳过。
    _handled_lock = host.mlto_handled_lock
    if _handled_lock is None:
        _handled_lock = threading.Lock()
        host.mlto_handled_lock = _handled_lock

    def _reserve_key(_key: str) -> bool:
        """原子地检查并占位一个 mid/long 处理 key；成功占位返回 True。"""
        with _handled_lock:
            if _key in handled:
                return False
            handled.add(_key)
            return True

    # [阶段4] 中线 SwingAgent 独立路径已废弃——中线分析由长线 thesis 的 mid_view 提供。
    # run_mid 参数保留兼容签名（调用方仍会传），但本函数不再做任何中线 LLM/开仓动作。
    # 历史上的 _swing_one 并行 LLM 路径曾是"3 killers"之一（参数不匹配的 TypeError
    # 被静默吞掉、整轮中线零开仓），现随 mid-into-long 合并彻底删除。
    if run_mid:
        # [2026-08-15 因子化] 因子路由开启时：中线宇宙逐币用活跃因子合成信号入场
        # （decide → execute_midlong_open source=factor_route）；未开启时保持占位语义。
        from backend.config.settings import MIDLONG_MID_VIA_FACTOR_ROUTE as _FR
        _mid_syms = list(dict.fromkeys(
            [str(s).upper() for s in (mid_universe or [])]
            or [str(s).upper() for s in symbols]
        ))
        if _FR and _mid_syms:
            for _m in _mid_syms:
                if not _reserve_key(f"{_m}:mid"):
                    continue
                try:
                    from backend.services.factor_engine.midlong_factor_route import (
                        factor_route_open,
                    )
                    _fr_dec = factor_route_open(
                        host=host,
                        session=session,
                        symbol=_m,
                        market_summary=market_summary,
                        portfolio=portfolio,
                        trading_mode=_trade_mode,
                    )
                    logger.info(
                        "[FactorRoute] %s action=%s score=%s opened=%s gate=%s | %s",
                        _m, _fr_dec.get("action"), _fr_dec.get("score"),
                        _fr_dec.get("opened"), _fr_dec.get("gate"),
                        (_fr_dec.get("reason") or "")[:110],
                    )
                except Exception as _fr_err:
                    logger.warning("[FactorRoute] %s 决策异常: %s", _m, _fr_err, exc_info=True)
        else:
            # 仅 reserve mid key，避免下游 MLTO 段误判 mid 未处理而重复触发。
            for _s in symbols:
                _reserve_key(f"{str(_s).upper()}:mid")

    # SwingDB 句柄曾供 _swing_one 使用；保留 import 兼容下游 _trend_one 的 DB 工厂。
    from backend.database.connection import SessionLocal as _SwingDB
    _swing_db = None  # type: ignore[assignment]

    # ═══ 长线 TrendAgent 独立决策（并行 LLM）═══
    # P3：TrendAgent 方向分析始终运行（AI 策略分析全链路），
    # MidLong v2 Single Writer：authority=mlto 时 Trend 只分析；authority=trend 时可开仓
    from backend.services.full_auto.midlong_executor import (
        execute_midlong_open,
        get_midlong_exec_authority,
        set_trend_hint,
    )
    _exec_auth = get_midlong_exec_authority()
    from backend.config.settings import MIDLONG_MLTO_CONTROLS_EXEC  # noqa: F401 — 兼容旧注释路径
    _active_tiers = host.current_ai_tiers or ["mid", "long"]
    _trend_analyze = os.getenv("MIDLONG_TREND_AGENT_ANALYZE", "true").lower() in (
        "1", "true", "yes", "on",
    )
    if run_long and "long" in _active_tiers and _trend_analyze:
        from backend.services.trend_agent import trend_agent, derive_trend_side
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _trend_one(sym_raw: str):
            """单个 symbol 的 TrendAgent 分析（含 LLM + 开仓 + 审计）。"""
            from backend.core.tenant import set_system_identity
            # [2026-08-04 修复] ThreadPoolExecutor worker 不继承调用线程的 ContextVar
            # （间歇性"无归属用户"/RLS 隐藏根因），线程内自设系统身份穿透 RLS。
            set_system_identity()
            sym_u = str(sym_raw).upper()
            _db_t = _SwingDB()
            try:
                host.inject_midlong_indicators(market_summary, sym_u, include_weekly=True)
                # [Phase 5] 模式切换（§7.2）：该交易对已有未平仓中长线仓位
                # → 进入模式 B 持仓管理分析（六维发展分析），不再重复做入场分析。
                try:
                    from backend.services.full_auto.midlong_position_manager import (
                        has_open_midlong_position,
                        manage_position,
                    )
                    _mgmt_acct = getattr(session, "paper_account_id", None) or getattr(session, "account_id", None)
                    if has_open_midlong_position(_db_t, _mgmt_acct, sym_u):
                        _mgmt_dec = manage_position(
                            _db_t, host=host, session=session, account_id=_mgmt_acct,
                            symbol=sym_u, position={},
                            market_summary=market_summary or {},
                            analyst_reports=analyst_reports or {},
                            trading_mode=_trade_mode,
                        )
                        return (
                            sym_u,
                            str(_mgmt_dec.get("action") or "manage_hold"),
                            int(_mgmt_dec.get("score", 0) or 0),
                            str(_mgmt_dec.get("direction") or "manage"),
                            str(_mgmt_dec.get("reasoning") or ""),
                            str(_mgmt_dec.get("hold_reason") or ""),
                        )
                except Exception as _mgmt_err:
                    logger.warning("[MidLong] 模式B持仓管理异常 %s: %s", sym_u, _mgmt_err, exc_info=True)
                # [2026-08-17 long_trend_v2] 长线方向判定由 V2 规则化 L1 接管，
                # 跳过旧 LLM TrendAgent（trend_agent.analyze_direction）。V2 多头单边，
                # L1=up 才 buy；否则 hold。返回与 _trend_result 兼容的 dict，后续流程不变。
                _v2_entry = None
                try:
                    from backend.services.long_trend_v2 import (
                        long_v2_enabled as _v2_on,
                        entry_signal as _v2_entry_signal,
                    )
                    if _v2_on():
                        _v2_entry = _v2_entry_signal(sym_u, market_summary or {})
                except Exception as _v2_se:
                    logger.debug("[TrendAgent][V2] long 信号接管跳过: %s", _v2_se)
                if _v2_entry is not None:
                    _trend_result = {
                        "should_open": bool(_v2_entry.get("should_open")),
                        "direction": str(_v2_entry.get("direction") or "neutral"),
                        "score": int(_v2_entry.get("score") or 0),
                        "hold_reason": str(_v2_entry.get("hold_reason") or ""),
                        "raw_should_open": bool(_v2_entry.get("should_open")),
                        "suggested_sl_pct": float(
                            _v2_entry.get("suggested_sl_pct") or 0.08
                        ),
                        "size_hint_mult": 1.0,
                        "reasoning": str(_v2_entry.get("reason") or ""),
                        "soft_open": False,
                    }
                else:
                    _t_side = derive_trend_side(sym_u, market_summary or {})
                    _trend_result = trend_agent.analyze_direction(
                        symbol=sym_u,
                        side=_t_side,
                        reports=analyst_reports or {},
                        market_envs=market_summary or {},
                        account_id=getattr(session, "paper_account_id", None),
                        portfolio=portfolio,
                        db=_db_t,
                        trading_mode=_trade_mode,
                        light_context=light_context,
                    ) or {}
                _trend_score = int(_trend_result.get("score", 0) or 0)
                _trend_dir = (_trend_result.get("direction") or "neutral").lower()
                # 供 Hub Trend 一致性 bonus（即使本轮 authority≠trend 也写入）
                try:
                    set_trend_hint(
                        sym_u,
                        should_open=bool(_trend_result.get("should_open")),
                        direction=_trend_dir,
                        score=_trend_score,
                    )
                except Exception:
                    pass
                if _trend_result.get("should_open", False) and _trend_dir == "long":
                    _trend_action = "buy"
                elif _trend_result.get("should_open", False) and _trend_dir == "short":
                    _trend_action = "sell"
                else:
                    _trend_action = "hold"
                # V2 接管时本路径即长线唯一开仓入口（source=mlto 过 Single Writer 门禁）。
                _v2_route = _v2_entry is not None
                if _trend_action in ("buy", "sell"):
                    if _exec_auth != "trend" and not _v2_route:
                        # Single Writer=mlto：Trend 只写 hint/证据，不开仓
                        logger.info(
                            "[MidLong] stage=fuse symbol=%s authority=%s source=trend "
                            "action=hold reason=evidence_only (writer=mlto)",
                            sym_u, _exec_auth,
                        )
                        _trend_action = "hold"
                    else:
                        logger.info(
                            "[MidLong] stage=fuse symbol=%s authority=%s source=%s "
                            "action=%s score=%d dir=%s soft=%s",
                            sym_u, _exec_auth, ("mlto" if _v2_route else "trend"),
                            _trend_action, _trend_score, _trend_dir,
                            bool(_trend_result.get("soft_open")),
                        )
                else:
                    logger.info(
                        "[TrendAgent独立] %s hold score=%d dir=%s why=%s raw_should=%s",
                        sym_u, _trend_score, _trend_dir,
                        _trend_result.get("hold_reason") or "unknown",
                        _trend_result.get("raw_should_open"),
                    )
                    # Phase4：Trend hold 也记失败 Intent，供信念复盘
                    try:
                        from backend.services.mlto.midlong_belief_loop import (
                            record_failed_intent,
                        )
                        from backend.services.decision_core.regime_agent import (
                            classify_regime,
                        )
                        _ms_h = (market_summary or {}).get(sym_u) or {}
                        _reg_h = classify_regime(
                            _ms_h if isinstance(_ms_h, dict) else {}
                        ).regime
                        # [P2-4] should_open=False 的 trend_hold 是「正常市场结论」
                        # 而非失败：每 2 分钟一轮循环若全记 failed_intent，200 条上限
                        # 会被噪音灌满，稀释真正需要复盘的失败样本。标记 noise=True。
                        _hold_why = str(
                            _trend_result.get("hold_reason") or "trend_hold"
                        )
                        record_failed_intent(
                            symbol=sym_u,
                            reason=_hold_why,
                            regime=_reg_h,
                            score=_trend_score,
                            authority=_exec_auth,
                            source="trend",
                            session_id=session_id,
                            noise=not bool(_trend_result.get("raw_should_open")),
                        )
                        # P0：统一「为何没开」审计（与信念 Intent 并行，供漏斗 KPI）
                        try:
                            from backend.services.mlto.midlong_direction_audit import (
                                record_decision_audit,
                            )
                            record_decision_audit(
                                outcome="skip",
                                stage="trend",
                                symbol=sym_u,
                                reason=_hold_why,
                                session_id=session_id,
                                tier="long",
                                source="trend",
                                authority=_exec_auth,
                                action="hold",
                                direction=_trend_dir,
                                score=_trend_score,
                                regime=_reg_h,
                            )
                        except Exception:
                            pass
                    except Exception:
                        pass

                if _trend_action in ("buy", "sell"):
                    # 2026-07-20：开仓前二次确认 symbol 仍在 session.symbols。
                    _cur_syms = {str(x).upper() for x in (getattr(session, "symbols", None) or [])}
                    if sym_u not in _cur_syms:
                        logger.info(
                            "[TrendAgent独立] %s 已从 session.symbols 移除，跳过开仓", sym_u,
                        )
                        _trend_action = "hold"
                    else:
                        _sl = float(_trend_result.get("suggested_sl_pct", 0.08) or 0.08)
                        _size_mult = float(_trend_result.get("size_hint_mult") or 1.0)
                        if _size_mult <= 0:
                            _size_mult = 1.0
                        execute_midlong_open(
                            host=host,
                            db=_db_t,
                            session=session,
                            source=("mlto" if _v2_route else "trend"),
                            symbol=sym_u,
                            action=_trend_action,
                            confidence=max(_trend_score, 50),
                            sl_pct=_sl,
                            tp_pct=_sl * 2,
                            market_summary=market_summary,
                            session_mode=_session_status,
                            tier="long",
                            trade_nature="trend_follow",
                            tranche_margin_pct=_size_mult,
                            tp_sl_proposal=_trend_result.get("tp_sl_proposal"),
                            invalidation_condition=(_trend_result.get("invalidation") or {}).get("condition", ""),
                            expected_hold_hours=_trend_result.get("expected_hold_hours", 0.0),
                            reason=(
                                "trend_soft_open" if _trend_result.get("soft_open")
                                else (_trend_result.get("hold_reason") or "trend_should_open")
                            ),
                            trading_mode=_trade_mode or "paper",
                        )
                host.persist_independent_scan_log(
                    account_id=getattr(session, "paper_account_id", None),
                    symbol=sym_u,
                    tier="long",
                    trade_nature="trend_follow",
                    action=_trend_action,
                    confidence=_trend_score,
                    reasoning=str(_trend_result.get("reasoning") or ""),
                    agent_source="trend_agent",
                    cited_fact_ids=_trend_result.get("cited_fact_ids"),
                    evidence_audit=_trend_result.get("evidence_audit"),
                    market_summary=market_summary,
                )
                return (sym_u, _trend_action, _trend_score, _trend_dir,
                        str(_trend_result.get("reasoning") or ""),
                        str(_trend_result.get("hold_reason") or ""))
            except Exception as _tr_err:
                logger.warning("[TrendAgent独立] %s long 失败: %s", sym_u, _tr_err, exc_info=True)
                return None
            finally:
                try:
                    _db_t.close()
                except Exception:
                    pass

        from backend.services.auto_coin_selector import get_fixed_symbols_for_session

        _long_targets = []
        # [2026-07-21 修复] 原来"排除法"读 session.auto_coin_symbols——这个 session
        # 对象是本次 tick 开始时加载的，可能已经持有了几分钟（LLM分析耗时），期间AI选币
        # 若通过另一条DB连接完成了注入/剔除提交，这里读到的就是过期快照，导致AI选的币
        # 因为"暂时不在过期快照的auto_coin_symbols里"被误判成固定币漏进长线（用户反馈
        # KBONK反复出现在tier=long日志的根因）。改为调用统一的正向白名单函数，每次都
        # 现查DB最新行，把过期窗口从"分钟级"压缩到"毫秒级"。
        _fixed_symbols = get_fixed_symbols_for_session(session_id, tier="long")
        for _s in symbols:
            _su = str(_s).upper()
            # 只有确认是"会话固定配置"的symbol才能进长线；AI选币(含任何未被明确
            # 认定为固定的symbol，比如已过期/残留的AI币)一律跳过。
            if _su not in _fixed_symbols:
                logger.debug(
                    "[TrendAgent独立] %s 非会话固定币种，跳过长线", _su,
                )
                continue
            host.inject_midlong_indicators(market_summary, _su, include_weekly=True)
            if _reserve_key(f"{_su}:long"):
                _long_targets.append(_su)

        if _long_targets:
            # [中长线合并] TrendAgent 全量并行（用户要求不设并发上限）：
            # 空响应根因是流式 safety cap 截断（已设 0 不截断），非并发本身。
            with ThreadPoolExecutor(max_workers=max(1, len(_long_targets))) as pool:
                futures = {pool.submit(_trend_one, s): s for s in _long_targets}
                for fut in as_completed(futures):
                    result = fut.result()
                    if result:
                        sym_u, action, score, tdir, reasoning, hold_reason = result
                        host.append_event(
                            session, "master_decision",
                            host.format_agent_event_detail(
                                sym_u, "长线", action,
                                metric_label="score", metric_value=score,
                                agent_label="TrendAgent独立",
                                reasoning=reasoning,
                                hold_reason=hold_reason,
                            ),
                        )
    # [阶段4] _swing_db 已废弃（原 _swing_one 路径删除），无需 close。

    # ═══ 中线持仓管理（模式 B）：AI 中线 swing 仓位与长线一样接入动态 TP/SL ═══
    # 修复根因：manage_position 此前只挂在 _trend_one（长线固定币），AI 中线持仓
    # （如 XPL swing）从未进入 LLM 复盘/收紧/分档止盈链路，TP/SL 永远静态。
    try:
        from concurrent.futures import ThreadPoolExecutor as _MTPE, as_completed as _MAC
        from backend.services.auto_coin_selector import get_fixed_symbols_for_session as _gfs_long
        from backend.services.full_auto.midlong_position_manager import (
            has_open_midlong_position as _has_midlong_pos,
            manage_position as _manage_pos,
        )
        _fixed_long_now = set(
            str(x).upper() for x in (_gfs_long(session_id, tier="long") or [])
        )
        _mgmt_acct = (
            getattr(session, "paper_account_id", None)
            or getattr(session, "account_id", None)
        )
        _mid_manage_targets = [
            str(s).upper() for s in symbols
            if str(s).upper() not in _fixed_long_now
        ]
        if _mid_manage_targets and _mgmt_acct:
            def _mid_manage_one(sym_raw: str):
                from backend.core.tenant import set_system_identity
                set_system_identity()
                sym_u = str(sym_raw).upper()
                _db_m = _SwingDB()
                try:
                    host.inject_midlong_indicators(
                        market_summary, sym_u, include_weekly=False
                    )
                    if not _has_midlong_pos(_db_m, _mgmt_acct, sym_u):
                        return None
                    _dec = _manage_pos(
                        _db_m,
                        host=host,
                        session=session,
                        account_id=_mgmt_acct,
                        symbol=sym_u,
                        position={},
                        market_summary=market_summary or {},
                        analyst_reports=analyst_reports or {},
                        trading_mode=_trade_mode,
                    )
                    return (
                        sym_u,
                        str(_dec.get("action") or "manage_hold"),
                        int(_dec.get("score", 0) or 0),
                        str(_dec.get("reasoning") or ""),
                    )
                except Exception as _me:
                    logger.warning("[MidLong] 中线持仓管理异常 %s: %s", sym_u, _me)
                    return None
                finally:
                    try:
                        _db_m.close()
                    except Exception:
                        pass

            with _MTPE(max_workers=max(1, len(_mid_manage_targets))) as _mpool:
                _mfuts = {_mpool.submit(_mid_manage_one, s): s for s in _mid_manage_targets}
                for _mf in _MAC(_mfuts):
                    _res = _mf.result()
                    if _res:
                        host.append_event(
                            session,
                            "master_decision",
                            host.format_agent_event_detail(
                                _res[0], "中线", _res[1],
                                metric_label="score", metric_value=_res[2],
                                agent_label="中线持仓管理",
                                reasoning=_res[3],
                            ),
                        )
    except Exception as _mid_mgmt_err:
        logger.debug("[MidLong] 中线持仓管理段跳过: %s", _mid_mgmt_err)

    # ═══ 长线 MLTO thesis 管理（仅 MIDLONG_THESIS_LEDGER_ENABLED 时）═══
    from backend.config.settings import MIDLONG_THESIS_LEDGER_ENABLED
    if not MIDLONG_THESIS_LEDGER_ENABLED:
        host.mlto_handled_keys = handled
        return

    # 独立轻量循环：thesis 维护留给 QAA 主循环，此处只做 Agent 决策
    if light_context:
        host.mlto_handled_keys = handled
        return

    # 保留 run_mid/run_long 已处理的 key，避免 MLTO 段重复调 SwingAgent
    orch_decs = host.last_orch_decisions or {}
    from backend.config.settings import MIDLONG_AI_MANDATORY
    # [2026-07-21 修复] 与上方 TrendAgent 独立段同源同法：正向白名单 + 每次现查DB，
    # 不再靠本 tick 长期持有的 session 对象上可能过期的 auto_coin_symbols 快照做排除。
    from backend.services.auto_coin_selector import (
        get_ai_mid_candidates_for_session,
        get_fixed_symbols_for_session,
    )
    _fixed_symbols = get_fixed_symbols_for_session(session_id, tier="long")
    _fixed_mid_symbols = get_fixed_symbols_for_session(session_id, tier="mid")
    # [2026-08-10 问题三] AI 中线候选：仅 mid lane 消费（与固定长线白名单正交）。
    # 候选为空时下方 tier='mid' 段自然跳过；mid 符号不参与长线 thesis（源头切断不变）。
    _ai_mid_symbols = set(get_ai_mid_candidates_for_session(session_id) or [])
    # 已开 mid 仓但候选人被刷掉：仍纳入 mid thesis/管仓，禁止「列表没了就没人管仓」
    try:
        _acct = getattr(session, "paper_account_id", None) or getattr(session, "account_id", None)
        if _acct:
            from backend.database.connection import SessionLocal as _CorePos
            from backend.services.full_auto.midlong_position_manager import (
                _open_midlong_positions as _omp,
            )
            _pdb = _CorePos()
            try:
                for _p in _omp(_pdb, int(_acct)) or []:
                    if str(_p.get("timeframe_tier") or "").lower() == "mid" or str(
                        _p.get("trade_nature") or ""
                    ).lower() == "swing":
                        _su = str(_p.get("symbol") or "").upper()
                        if _su:
                            _ai_mid_symbols.add(_su)
            finally:
                try:
                    _pdb.close()
                except Exception:
                    pass
    except Exception:
        pass
    _ai_mid_new_only = set(get_ai_mid_candidates_for_session(session_id) or [])
    # 中线可分析集合 = 固定中线 ∪ AI中线候选 ∪ 续管持仓（勿用 long 白名单冒充 mid）
    _mid_allowed = set(_fixed_mid_symbols) | set(_ai_mid_symbols)
    # [2026-08-14 F3 整改] 最终防线：中线宇宙 ∩ 数据中心 catalog 可交易集。
    # fail-closed——即使选币链路再被污染，非 catalog symbol 到不了分析层。
    # （事故：CSCO/CYS 股票代码经 AI 中线候选进入 _mid_allowed 并被分析。）
    try:
        from backend.services.kline_sync_meta import list_catalog_symbols
        _trading: set = set()
        for _ex in ("asterdex", "binance", "hyperliquid", "bybit", "okx"):
            _trading.update(str(s).strip().upper() for s in (list_catalog_symbols(_ex, status="trading") or []))
        if _trading:
            _dropped = _mid_allowed - _trading
            _mid_allowed = _mid_allowed & _trading
            if _dropped:
                logger.warning(
                    "[MLTO] F3 防线剔除 %d 个非 catalog 中线标的: %s",
                    len(_dropped), sorted(_dropped),
                )
    except Exception as e:  # noqa: BLE001
        logger.warning("[MLTO] F3 防线 catalog 读取失败(保持原集合): %s", e)
    _ana_db = None
    try:
        from backend.database.connection import AnalyticsSessionLocal
        # [2026-08-17] run_mlto_tick 已删（旧 MLTO thesis LLM 下线）。
        # _thesis_llm_one 因长线/中线 thesis 任务均被跳过而永不执行，此段为空转。

        _ana_db = None

        def _thesis_llm_one(sym_u, slot_action, tier):
            # 每符号独立连接 + 独立 run_mlto_tick（LLM 60-90s），线程内自闭环。
            from backend.core.tenant import set_system_identity
            # [2026-08-04 修复] 同 _trend_one：worker 线程无 HTTP/主线程 ContextVar，
            # 不设身份则 SessionLocal 查询受 RLS fail-closed → thesis LLM 配置解析失败
            # → 规则回退 → direction=neutral / conviction 归零 → 中长线永不开仓。
            set_system_identity()
            _db = AnalyticsSessionLocal()
            try:
                return run_mlto_tick(
                    session_id=session_id,
                    symbol=sym_u,
                    tier=tier,
                    market_summary=market_summary or {},
                    analyst_reports=analyst_reports or {},
                    session=session,
                    db=_db,
                    portfolio=portfolio,
                    persistence_state=host.midlong_persistence_state,
                    slot_action=slot_action,
                    trading_mode=mode,
                )
            finally:
                try:
                    _db.close()
                except Exception:
                    pass

        # [2026-08-17 long_trend_v2] V2 接管长线：关闭旧 MLTO thesis LLM。
        # 长线入场由规则化 L1(entry_signal) 驱动，不再跑 run_mlto_tick 的 LLM thesis。
        # 副作用：mid_view(中线择时子结构)不再刷新——中线仍靠 FactorRoute + 规则管理。
        try:
            from backend.services.long_trend_v2 import long_v2_enabled as _v2_long_on
            _v2_long_on = bool(_v2_long_on())
        except Exception:
            _v2_long_on = False
        try:
            from backend.config.settings import MIDLONG_MID_VIA_MLTO as _mid_via_mlto
        except Exception:
            _mid_via_mlto = True

        _thesis_jobs: list = []
        for sym in symbols:
            sym_u = str(sym).upper()
            # 先注入基础中长线指标（1h/4h/1d）
            host.inject_midlong_indicators(market_summary, sym_u, include_weekly=False)
            od = orch_decs.get(sym_u) or orch_decs.get(sym)
            actions = getattr(od, "slot_actions", {}) if od else {}
            slots = getattr(od, "recommended_slots", None) if od else None
            _active_tiers = host.current_ai_tiers or ["mid", "long"]
            for tier in ("mid", "long"):
                if tier not in _active_tiers:
                    continue
                # mid：固定币 + AI中线≤3；long：仅固定币
                if tier == "mid":
                    if not _mid_via_mlto:
                        # [2026-08-17] 中线规则驱动：MIDLONG_MID_VIA_MLTO=false 时不再提交
                        # 中线 thesis LLM 任务（FactorRoute + 规则管理接管，thesis 是空转 no-op）。
                        continue
                    if not run_mid:
                        continue
                    if sym_u not in _mid_allowed:
                        logger.debug("[MLTO] %s 不在中线宇宙(固定∪AI)，跳过中线 thesis", sym_u)
                        continue
                else:
                    if _v2_long_on:
                        # V2 接管：长线不再提交 LLM thesis 任务（关闭旧逻辑）。
                        continue
                    if light_context and not run_long:
                        continue
                    if sym_u not in _fixed_symbols:
                        logger.debug("[MLTO] %s 非会话固定币种，跳过长线 thesis", sym_u)
                        continue
                host.inject_midlong_indicators(
                    market_summary, sym_u,
                    include_weekly=(tier != "mid"),
                )
                _ms = market_summary.get(sym_u) or {}
                if tier != "mid" and not _ms.get("indicators_1w"):
                    logger.info("[MLTO] %s 本币周线缺失，拒绝注入大盘参考（fail-closed）", sym_u)
                if MIDLONG_AI_MANDATORY:
                    slot_action = "create"
                else:
                    slot_action = (actions or {}).get(tier) or "observe"
                    if slots is not None and tier not in (slots or []):
                        slot_action = "observe"
                _thesis_jobs.append((sym_u, slot_action, tier))

        # [中长线合并] thesis 更新并行（用户要求不设并发上限）：
        # 每符号独立线程 + 独立 DB 连接，run_mlto_tick（LLM 60-90s）并发执行。
        _thesis_futs: dict = {}
        if _thesis_jobs:
            with ThreadPoolExecutor(max_workers=max(1, len(_thesis_jobs))) as _pool:
                for _sym_u, _slot_action, _tier in _thesis_jobs:
                    _thesis_futs[(_sym_u, _tier)] = (
                        _pool.submit(_thesis_llm_one, _sym_u, _slot_action, _tier),
                        _slot_action,
                        _tier,
                    )

        for (sym_u, tier), (_fut, slot_action, _tier) in _thesis_futs.items():
                try:
                    key = f"{sym_u}:{tier}"
                    _mlto_result = _fut.result()
                    # 与 _reserve_key 同一把锁，避免与独立循环竞态分叉
                    with _handled_lock:
                        handled.add(key)
                    logger.info("[MLTO] maintain tick %s %s slot=%s", sym_u, tier, slot_action)
                    _ana_db = AnalyticsSessionLocal()

                    # 修复（2026-07-02）：MLTO 结果推送到前端事件流
                    # 之前 MLTO 只记日志不推事件，导致前端 AI 决策日志看不到中长线
                    # MidLong v2：authority=mlto 时 thesis 段推事件并可开仓；
                    # authority=trend 时 thesis 只更新论点，开仓由 TrendAgent 负责。
                    if _exec_auth == "mlto" and _mlto_result and hasattr(_mlto_result, "action"):
                        _mlto_tier_lbl = "中线" if tier == "mid" else "长线"
                        _mlto_action = (_mlto_result.action or "hold").lower()
                        if _mlto_action == "wait":
                            _mlto_action = "hold"
                        _mlto_conf = int(getattr(_mlto_result, "confidence", 0) or 0)
                        _mlto_reason = (_mlto_result.reason or "")[:120] if hasattr(_mlto_result, "reason") else ""
                        _mlto_margin = float(getattr(_mlto_result, "tranche_margin_pct", 0) or 0)
                        host.append_event(
                            session, "master_decision",
                            f"🎯 {sym_u}[{_mlto_tier_lbl}]: {_mlto_action} (置信={_mlto_conf}%) | "
                            f"[MLTO] margin={_mlto_margin:.0%} {_mlto_reason}"
                        )

                    # ── MidLong v2：仅 authority=mlto 时 MLTO 可新开；close 始终允许 ──
                    if _mlto_result and hasattr(_mlto_result, "action"):
                        _mlto_act = (_mlto_result.action or "hold").lower()
                        # [2026-08-17 long_trend_v2] 长线开/平决策由 V2 规则化 L1 接管：
                        # 覆盖 LLM thesis 的 buy/sell/close（旧逻辑）。V2 多头单边，L1=up 才 buy，
                        # 否则 hold；退出只认 V2 结构破坏/Chandelier（manage_long_position），
                        # 不再让 LLM invalidation 平掉长线仓。
                        _mlto_v2_sl = None
                        _mlto_v2_reason = ""
                        if (tier or "").lower() == "long" and _mlto_act in ("buy", "sell", "close"):
                            try:
                                from backend.services.long_trend_v2 import (
                                    long_v2_enabled,
                                    entry_signal,
                                )
                                if long_v2_enabled():
                                    if _mlto_act == "close":
                                        _mlto_act = "hold"
                                        _v2sig = {
                                            "should_open": False,
                                            "hold_reason": "LLM invalidation close 已跳过(V2 结构破坏退出接管)",
                                        }
                                    else:
                                        _v2sig = entry_signal(sym_u, market_summary or {})
                                    if _v2sig.get("should_open"):
                                        _mlto_act = "buy"
                                        _mlto_v2_sl = float(
                                            _v2sig.get("suggested_sl_pct") or 0.08
                                        )
                                        _mlto_v2_reason = str(_v2sig.get("reason") or "")
                                        logger.info(
                                            "[MLTO][V2] %s long 规则化 buy: %s",
                                            sym_u, _mlto_v2_reason,
                                        )
                                    else:
                                        _mlto_act = "hold"
                                        logger.info(
                                            "[MLTO][V2] %s long 规则化 hold: %s",
                                            sym_u, _v2sig.get("hold_reason"),
                                        )
                            except Exception as _v2_ov_err:
                                logger.debug(
                                    "[MLTO][V2] long 入场覆盖跳过: %s", _v2_ov_err
                                )
                        if _mlto_act == "close":
                            # [阶段3e] invalidation 驱动 close：直接平掉该 symbol 仓位。
                            try:
                                from backend.database.connection import SessionLocal as _CloseDB
                                from backend.database.models import FullAutoSession as _FAS
                                _close_db = _CloseDB()
                                _close_session = _close_db.query(_FAS).filter(
                                    _FAS.session_id == session_id
                                ).first() or session
                                _mlto_close_reason = (
                                    (getattr(_mlto_result, "reason", "") or "")
                                    [:120] or "mlto_invalidation"
                                )
                                _closed = _mlto_close_symbol(
                                    db=_close_db,
                                    session=_close_session,
                                    symbol=sym_u,
                                    thesis=getattr(_mlto_result, "thesis", None),
                                    reason=_mlto_close_reason,
                                )
                                if _closed:
                                    logger.info(
                                        "[MLTO] invalidation close 执行 %s %s | %s",
                                        sym_u, tier, _mlto_close_reason,
                                    )
                            except Exception as _mlto_close_err:
                                logger.warning(
                                    "[MLTO] invalidation close 失败 %s %s: %s",
                                    sym_u, tier, _mlto_close_err, exc_info=True,
                                )
                            finally:
                                try:
                                    _close_db.close()
                                except Exception:
                                    pass
                        elif _mlto_act in ("buy", "sell"):
                            if _exec_auth != "mlto":
                                logger.info(
                                    "[MidLong] stage=fuse symbol=%s authority=%s source=mlto "
                                    "action=hold reason=evidence_only (writer=trend)",
                                    sym_u, _exec_auth,
                                )
                            else:
                                _exec_db = None
                                try:
                                    from backend.database.connection import SessionLocal as _ExecDB
                                    from backend.database.models import FullAutoSession as _FAS
                                    _exec_db = _ExecDB()
                                    _exec_session = _exec_db.query(_FAS).filter(
                                        _FAS.session_id == session_id
                                    ).first()
                                    if not _exec_session:
                                        _exec_session = session
                                    _mlto_sl = (
                                        _mlto_v2_sl
                                        if _mlto_v2_sl is not None
                                        else float(getattr(_mlto_result, "sl_pct", 0) or 0.05)
                                    )
                                    _mlto_tp = float(getattr(_mlto_result, "tp_pct", 0) or 0.10)
                                    _mlto_conf = int(getattr(_mlto_result, "confidence", 0) or 50)
                                    _mlto_margin_pct = float(
                                        getattr(_mlto_result, "tranche_margin_pct", 0) or 0
                                    )
                                    # [2026-08-10 问题三] AI 中线槽位 ≤3 二次校验：候选查询
                                    # 已按槽位截断，此处防同 tick 多候选并发开仓全部看到空槽。
                                    if tier == "mid":
                                        # 固定币中线可新开；AI 币仅当前候选可新开，否则只续管
                                        if (
                                            sym_u not in _fixed_mid_symbols
                                            and sym_u not in _ai_mid_new_only
                                        ):
                                            logger.info(
                                                "[MLTO] %s 已不在 AI 中线候选，禁止新开只续管 "
                                                "(ai_mid_hold_only)",
                                                sym_u,
                                            )
                                            continue
                                        # AI 中线槽位 ≤3 只约束非固定币
                                        if sym_u not in _fixed_mid_symbols:
                                            try:
                                                from backend.services.auto_coin_selector import (
                                                    count_open_ai_mid_positions,
                                                )
                                                _acc_mid = getattr(
                                                    _exec_session, "paper_account_id", None
                                                )
                                                if count_open_ai_mid_positions(
                                                    db=_exec_db, account_id=_acc_mid
                                                ) >= 3:
                                                    logger.info(
                                                        "[MLTO] %s AI 中线槽位已满(≥3)，拒绝新开 "
                                                        "(ai_mid_slot_full)",
                                                        sym_u,
                                                    )
                                                    host.append_event(
                                                        _exec_session, "master_decision",
                                                        f"🚫 {sym_u}[中线]: AI 中线槽位已满(≤3)，拒绝新开",
                                                    )
                                                    continue
                                            except Exception as _slot_err:
                                                logger.debug(
                                                    "[MLTO] AI 中线槽位校验跳过: %s", _slot_err
                                                )
                                    _th_exec = getattr(_mlto_result, "thesis", None)
                                    _hub_exec = getattr(_mlto_result, "hub", None)
                                    # [v6 S2-7 接入] regime_suggestion.trailing → 写入
                                    # 持仓 exit_state（PEO 分档止盈引擎按 ATR 追踪）。
                                    _rs_exec = (
                                        getattr(_th_exec, "regime_suggestion", None)
                                        if _th_exec is not None else None
                                    )
                                    _tp_sl_prop = None
                                    if isinstance(_rs_exec, dict) and _rs_exec.get("trailing"):
                                        _tp_sl_prop = {"trailing_atr_mult": 2.0}
                                    execute_midlong_open(
                                        host=host,
                                        db=_exec_db,
                                        session=_exec_session,
                                        source="mlto",
                                        symbol=sym_u,
                                        action=_mlto_act,
                                        confidence=_mlto_conf,
                                        sl_pct=_mlto_sl,
                                        tp_pct=_mlto_tp,
                                        market_summary=market_summary,
                                        session_mode=getattr(session, "status", "running"),
                                        tier=tier,
                                        trade_nature="trend_follow",
                                        tranche_margin_pct=_mlto_margin_pct,
                                        reason=(
                                            (_mlto_v2_reason or "")
                                            or (getattr(_mlto_result, "reason", "") or "")
                                        )[:80],
                                        trading_mode=_trade_mode or "paper",
                                        thesis_dir=str(getattr(_th_exec, "direction", "") or ""),
                                        hub_dir=str(getattr(_hub_exec, "direction", "") or ""),
                                        hub_mode=str(getattr(_hub_exec, "mode", "") or ""),
                                        dir_src=str(getattr(_hub_exec, "dir_src", "") or ""),
                                        tp_sl_proposal=_tp_sl_prop,
                                    )
                                except Exception as _mlto_exec_err:
                                    logger.warning(
                                        "[MLTO] 独立开仓失败 %s %s: %s",
                                        sym_u, tier, _mlto_exec_err,
                                    )
                                    try:
                                        from backend.services.mlto.midlong_direction_audit import (
                                            record_decision_audit,
                                        )
                                        record_decision_audit(
                                            outcome="skip",
                                            stage="exec",
                                            symbol=sym_u,
                                            reason=(
                                                f"mlto_exec_exception:"
                                                f"{type(_mlto_exec_err).__name__}:"
                                                f"{_mlto_exec_err}"
                                            )[:160],
                                            session_id=str(
                                                getattr(session, "session_id", "") or ""
                                            ),
                                            tier=str(tier or "").lower(),
                                            action=str(_mlto_act or ""),
                                            authority="mlto",
                                        )
                                    except Exception:
                                        pass
                                finally:
                                    if _exec_db is not None:
                                        try:
                                            _exec_db.close()
                                        except Exception:
                                            pass
                        # else hold: thesis 已更新，不开仓

                    # ── Fix C（2026-07-03）：长线 MLTO 决策补写 AIDecisionLog ──
                    # 长线走 MLTO 维护 tick，此前只更新 thesis + 推 session 事件，不写
                    # AIDecisionLog → 前端"AI策略日志"(model-chat) 永远看不到长线分析。
                    # 这里补写一条 tier=long 决策日志（与 _run_analyst_system 8493 同构），
                    # 让长线分析和短线/中线一样出现在 AI 策略日志里。
                    try:
                        _acc_id = getattr(session, "paper_account_id", None)
                        if _acc_id and _mlto_result and hasattr(_mlto_result, "action"):
                            from backend.database.models import AIDecisionLog as _AIDL
                            _th = getattr(_mlto_result, "thesis", None)
                            _op = (_mlto_result.action or "hold").lower()
                            if _op == "wait":
                                _op = "hold"
                            if _op not in ("buy", "sell", "hold", "close", "reduce"):
                                _op = "hold"
                            _conf_i = int(getattr(_mlto_result, "confidence", 0) or 0)
                            _l_dir = getattr(_th, "direction", "neutral") if _th else "neutral"
                            _l_bias = {"long": "bullish", "short": "bearish"}.get(_l_dir, "neutral")
                            _reason_txt = (getattr(_mlto_result, "reason", "") or "")[:500]
                            _summary = (getattr(_th, "thesis_summary", "") or "") if _th else ""
                            _reasoning_full = (
                                getattr(_th, "reasoning_content", "") or _summary or _reason_txt
                            )[:4000]
                            # 真实账本数据：不再用 0 占位（total_balance/prev_portion）。
                            _real_balance = 0.0
                            _prev_portion = 0.0
                            _target_portion = 0.0
                            try:
                                from backend.database.connection import SessionLocal as _BalDB
                                from backend.database.models import PaperPosition as _PP, PaperBalance as _PB
                                _bal_db = _BalDB()
                                try:
                                    _pb = _bal_db.query(_PB).filter(
                                        _PB.account_id == _acc_id
                                    ).first()
                                    _real_balance = float(
                                        getattr(_pb, "total_equity", 0) or 0
                                    )
                                    if _real_balance > 0:
                                        _open = _bal_db.query(_PP).filter(
                                            _PP.account_id == _acc_id,
                                            _PP.symbol == sym_u,
                                            _PP.status == "open",
                                        ).all()
                                        _notional = sum(
                                            float(getattr(p, "size", 0) or 0)
                                            * float(getattr(p, "mark_price", 0) or 0)
                                            for p in _open
                                        )
                                        _prev_portion = _notional / _real_balance
                                finally:
                                    _bal_db.close()
                                if _op in ("buy", "sell"):
                                    _target_portion = max(
                                        0.0,
                                        min(
                                            1.0,
                                            float(
                                                getattr(
                                                    _mlto_result, "tranche_margin_pct", 0
                                                ) or 0
                                            ),
                                        ),
                                    )
                            except Exception as _bal_err:
                                logger.debug(
                                    "[MLTO] 长线决策日志账本查询跳过: %s", _bal_err
                                )
                            _dec_log_long = _AIDL(
                                account_id=_acc_id,
                                symbol=sym_u,
                                operation=_op,
                                reason=_reason_txt or f"[MLTO] {_op} {sym_u}",
                                reasoning_snapshot=_reasoning_full,
                                executed="false",
                                decision_source="mlto",
                                prev_portion=_prev_portion,
                                target_portion=_target_portion,
                                total_balance=_real_balance,
                                decision_snapshot=json.dumps({
                                    "trade_nature": "trend",
                                    "tier": tier,
                                    "confidence": _conf_i,
                                    "reasoning": _summary[:2000],
                                    "agent_source": "mlto",
                                    "tranche_margin_pct": float(getattr(_mlto_result, "tranche_margin_pct", 0) or 0),
                                }, ensure_ascii=False),
                                long_bias=_l_bias,
                                long_confidence=float(_conf_i) / 100.0,
                            )
                            _ana_db.add(_dec_log_long)
                            _ana_db.commit()
                    except Exception as _ldl_err:
                        try:
                            _ana_db.rollback()
                        except Exception:
                            pass
                        logger.warning("[MLTO] 长线 AIDecisionLog 写入跳过 %s: %s", sym_u, _ldl_err)
                except Exception as _mt_err:
                    logger.debug("[MLTO] maintain %s %s: %s", sym_u, tier, _mt_err)
                finally:
                    # [P2-2 修复] 每个 symbol 迭代结束即关闭连接。
                    # 此前 _ana_db 在循环内反复重赋值（AnalyticsSessionLocal()），
                    # 却只在循环结束后的外层 finally 统一 close 一次 → 每轮 N 个
                    # symbol 泄漏 N-1 条连接。此处每轮迭代自闭环，外层 finally 兜底。
                    if _ana_db is not None:
                        try:
                            _ana_db.close()
                        except Exception:
                            pass
                        _ana_db = None
        host.mlto_handled_keys = handled
    except Exception as exc:
        logger.debug("[MLTO] session maintain skip: %s", exc)
    finally:
        if _ana_db is not None:
            try:
                _ana_db.close()
            except Exception:
                pass

