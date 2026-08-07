"""
长线独立循环（含 mid_view）— 整改#8 midlong_loop 拆分 + 阶段4 中-并入-长。

从 full_auto_trading_service._run_midlong_independent 迁出；
monolith 保留 thin shim 转发。

[阶段4] 原"中线 SwingAgent 独立分析"路径已废弃——中线分析能力由长线 thesis 的
mid_view 子结构提供（Phase 2 起 qual_layer prompt 同时产出 long + mid_view）。
本循环现仅处理 long（其 thesis 内嵌 mid_view），不再单独调度 SwingAgent。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.services.full_auto_trading_service import FullAutoTradingService

logger = logging.getLogger(__name__)


def run_midlong_independent(svc: "FullAutoTradingService", session_id: str, tick: int) -> None:
    """轻量 long tick：TrendAgent + MLTO thesis（含 mid_view）+ 独立开单。"""
    self = svc
    # [C1] 中长线循环是 APScheduler 后台循环,不在 HTTP 请求上下文。设 system_identity
    # 覆盖整轮(含下方两次 db = SessionLocal() 重连模式 + _run_midlong_active_exit)。
    from backend.core.tenant import set_system_identity
    set_system_identity()
    from backend.database.connection import SessionLocal
    from backend.database.models import FullAutoSession
    from backend.services.tier_tick_scheduler import get_due_ai_tiers

    db = SessionLocal()
    try:
        session = db.query(FullAutoSession).filter(
            FullAutoSession.session_id == session_id
        ).first()
        if not session:
            logger.warning("[MidLongAgent独立] session 不存在 %s，提前 return", session_id)
            return
        if session.status not in ("running", "defensive"):
            logger.warning("[MidLongAgent独立] session.status=%s 非 running/defensive，提前 return %s", session.status, session_id)
            return
        due = get_due_ai_tiers(session_id)
        if not due:
            # 2026-07-20：诊断 due 为空的根因（曾在此静默 return 导致 midlong 从不执行）
            try:
                from backend.services.tier_tick_scheduler import status as _tier_status
                _ts = _tier_status(session_id)
                logger.warning(
                    "[MidLongAgent独立] due 为空，提前 return %s tier_status=%s", session_id, _ts
                )
            except Exception:
                logger.warning("[MidLongAgent独立] due 为空，提前 return %s", session_id)
            # [P2-6 修复] due 为空只跳过「入场分析」，主动退出（bias 反转 / no-progress）
            # 必须照常执行——此前直接 return 连 active exit 一起跳过，有仓时只能死等 SL/TP。
            try:
                if session is not None:
                    _exit_ms = (
                        session.last_market_summary
                        if isinstance(session.last_market_summary, dict)
                        else {}
                    )
                    self._run_midlong_active_exit(db, session, _exit_ms)
            except Exception as _exit_err:
                logger.debug("[MidLongExit] due 为空主动退出检查跳过: %s", _exit_err)
            try:
                self._safe_commit(db, "midlong_active_exit_only", session=session)
            except Exception as _cm_err:
                logger.debug("[MidLongAgent独立] 主动退出后提交跳过: %s", _cm_err)
            return
        self._current_ai_tiers = list(due)
        # MidLong v2 C7：仅在独立 midlong tick 开始时清空占位键（analyst 禁止清空）
        try:
            _lock = getattr(self, "_mlto_handled_lock", None)
            if _lock is None:
                import threading
                _lock = threading.Lock()
                self._mlto_handled_lock = _lock
            handled = getattr(self, "_mlto_handled_keys", None)
            if not isinstance(handled, set):
                handled = set()
                self._mlto_handled_keys = handled
            with _lock:
                handled.clear()
        except Exception as _clr_err:
            logger.debug("[MidLongAgent独立] handled_keys clear skip: %s", _clr_err)
        # [源头切断] 长线只用固定交易对,绝不含 auto-coin
        from backend.services.auto_coin_selector import get_fixed_symbols_for_session
        _session_id = getattr(session, "session_id", None)
        _fixed = get_fixed_symbols_for_session(_session_id, db) if _session_id else set()
        symbols = list(_fixed) if _fixed else self._resolve_session_trade_symbols(session, db)
        logger.info(f"[MidLongAgent独立] tick#{tick} 固定符号={symbols}(auto-coin已从源头切断)")
        market_summary = session.last_market_summary if isinstance(session.last_market_summary, dict) else {}
        try:
            fresh = self._scan_markets(db, symbols)
            if isinstance(fresh, dict) and fresh:
                # [P1-修复] 原实现 {**market_summary, **fresh} 用 scan dict 整体替换
                # symbol 条目，而 scan dict 不含 price_change_1h/24h_pct、volatility_pct
                # → 独立循环里 classify_regime 三字段恒 0 → 恒判 ranging → 长线被禁开。
                # 改为逐 symbol 深合并：scan 新值覆盖同名字段，已有字段（如主循环
                # merge_snapshot 写入的 price_change_*）保留。
                market_summary = {**market_summary}
                for _sym, _entry in fresh.items():
                    _base = market_summary.get(_sym)
                    if isinstance(_base, dict) and isinstance(_entry, dict):
                        market_summary[_sym] = {**_base, **_entry}
                    else:
                        market_summary[_sym] = dict(_entry) if isinstance(_entry, dict) else _entry
                session.last_market_summary = market_summary
                db.flush()
        except Exception as _scan_err:
            logger.debug("[MidLongAgent独立] 市场扫描跳过: %s", _scan_err)
        if symbols:
            self._ensure_market_prices(market_summary, symbols)
        # 编排器结果在 OrchBG 缓存里，合并进 market_summary（含 XPL 等非 session.symbols 币）
        for _sym in symbols:
            # 修复4：跳过数据不可靠的品种
            _sym_data = market_summary.get(_sym) or {}
            if not _sym_data.get("data_reliable", True) or _sym_data.get("data_stale"):
                logger.info("[MidLongAgent独立] %s 数据不可靠/过期，跳过分析", _sym)
                continue
            _cached = self._market_scan_cache.get(_sym) or {}
            if isinstance(_cached, dict) and _cached.get("orchestrator"):
                market_summary.setdefault(_sym, {})
                if not market_summary[_sym].get("orchestrator"):
                    market_summary[_sym]["orchestrator"] = _cached["orchestrator"]
        logger.info(
            f"[MidLongAgent独立] tick#{tick} {session_id} tiers={due} symbols={len(symbols)}"
        )
        # 阶段一 A3：扫描吞吐。原实现每 tick 只扫 1 个币且 mid/long 交替，6 个币轮一圈
        # 需数分钟、长线 tick 90s → 覆盖率极低，是中长线"几乎不开仓"的结构性原因之一。
        # 改为每 tick 扫 MIDLONG_SCAN_BATCH 个币（默认 3；总开关关闭时回退为 1），游标
        # 每 tick 前进一个 batch，滚动覆盖全部币种。循环受 _midlong_loop_running 串行
        # 保护，不会并发叠加；实际 LLM 调用数 = batch × 到期侧数，45s 间隔下可承受。
        try:
            from backend.config.settings import MIDLONG_SCAN_BATCH
            _batch_n = max(1, int(MIDLONG_SCAN_BATCH or 1))
        except Exception:
            _batch_n = 1
        _cursor = self._midlong_symbol_cursor.get(session_id, 0)
        if symbols:
            _n = min(_batch_n, len(symbols))
            _sym_one = [symbols[(_cursor + i) % len(symbols)] for i in range(_n)]
        else:
            _sym_one = []
        # [阶段4] mid 不再单独调度——中线由长线 thesis 的 mid_view 统一产出。
        # _run_mid 强制 False：保留调度器 due 里的 mid 信号仅作游标推进/可观测性，
        # 实际 LLM/开仓只走 long（含 mid_view）。
        _run_mid = False
        _run_long = "long" in due
        # 游标每 tick 前进一个 batch，保证滚动覆盖全部 symbol（不再仅在 long 轮才前进）。
        if symbols:
            self._midlong_symbol_cursor[session_id] = (_cursor + len(_sym_one)) % len(symbols)
        _trade_mode = (getattr(session, "trading_mode", None) or "paper").strip().lower()
        logger.info(
            "[MidLongAgent独立] batch=%s mid=deprecated long=%s scan_n=%d",
            _sym_one, _run_long, len(_sym_one),
        )
        _portfolio = self._build_portfolio_for_agents(db, session)
        _pos_list = _portfolio.get("positions") or []
        if _pos_list:
            _tier_counts: dict = {}
            for _pp in _pos_list:
                _t = (_pp.get("timeframe_tier") or _pp.get("trade_nature") or "?").lower()
                _tier_counts[_t] = _tier_counts.get(_t, 0) + 1
            logger.info(
                "[MidLongAgent独立] cross_tier portfolio=%d tiers=%s",
                len(_pos_list), _tier_counts,
            )
        # ── 事务卫生（2026-07-09 修复 midlong_independent_tick 连接泄漏）──
        # 本方法原先全程只 checkout 一条 db 连接，并在下面 _maintain（数分钟 Swing/Trend
        # LLM，日志 read≈240s）期间一直占着它。pool_pre_ping 只在"从池取连接"时校验、
        # 中途不生效；这条长时间挂着的连接被服务端掐断（含我们为堵泄漏配的 90s
        # idle_in_transaction 超时）后，末尾 commit 必报 "Can't reconnect until invalid
        # transaction is rolled back"（重启前全天 25 次）。真正开仓走独立的 _swing_db、
        # 与本连接无关，故这里：
        #   1) 长 LLM 段【之前】先提交(持久化 last_market_summary 等)并【关闭】主连接、还池；
        #   2) 长 LLM 段【之后】开一条【全新】连接(取用时自带 pre_ping 校验)做主动退出+落库。
        # 关闭前先触达 event_log 强制加载：expire_on_commit=False 下已加载属性不失效，
        # _maintain 期间 session 虽 detached、但读缓存属性/在内存改 event_log 均安全，
        # 稍后用新连接 merge 回去持久化。
        try:
            _ = session.event_log  # 预加载，避免 detached 后 _append_event 触发懒加载报错
        except Exception:
            pass
        # 2026-07-20：commit 前先 rollback 清理可能的事务错误状态。
        # _scan_markets / _build_portfolio_for_agents 等操作可能因连接被服务端掐断
        # 而使 session 进入 PendingRollbackError 状态，直接 commit 会抛
        # "Can't reconnect until invalid transaction is rolled back"。
        # 先 rollback 清理，再 commit（commit 无待持久化数据时是 no-op，安全）。
        try:
            db.rollback()
        except Exception:
            pass
        self._safe_commit(db, "midlong_pre_llm", session=session)
        try:
            db.close()
        except Exception:
            pass
        # 修复5: 读 AlphaBus 短线 overlay（短线近期方向辅助中线决策）
        _short_overlay: dict = {}
        try:
            import time as _time

            from backend.services.bus.alpha_bus import get_default_alpha_bus
            from backend.services.contracts.types import Horizon
            _bus = get_default_alpha_bus()
            for _s in _sym_one:
                _insight = _bus.latest_insight(Horizon.SHORT, _s.upper())
                if _insight:
                    _age = (_time.time() * 1e9 - _insight.ts_ns) / 1e9
                    if _age < 7200:  # 只用 2 小时内的短线信号
                        _short_overlay[_s.upper()] = {
                            "direction": _insight.direction.value,
                            "confidence": round(_insight.confidence, 2),
                            "age_sec": int(_age),
                        }
            if _short_overlay:
                logger.info("[MidLongAgent独立] short overlay: %s", _short_overlay)
                if isinstance(market_summary, dict):
                    for _sym_key, _ov in _short_overlay.items():
                        if _sym_key not in market_summary:
                            market_summary[_sym_key] = {}
                        if isinstance(market_summary.get(_sym_key), dict):
                            market_summary[_sym_key]["short_overlay"] = _ov
        except Exception:
            pass
        # [2026-07-16 修复 DetachedInstanceError] _maintain_mlto_theses_for_session 内部会
        # 跑长 LLM（可达数百秒），期间 db 的底层连接可能因连接池 dispose（其他 tick 异常
        # 触发的 engine.dispose()）或回收而失效，导致绑定的 session 对象 detached，函数内
        # getattr(session,"session_id") 触发懒加载时抛 DetachedInstanceError，并被上层
        # 再次 engine.dispose() 形成连锁。修复：调用前用当前 db 重新 merge/reload session，
        # 确保 session 绑定在可用连接上。
        try:
            session = db.merge(session)
        except Exception:
            session = db.query(FullAutoSession).filter(
                FullAutoSession.session_id == session_id
            ).first()
            if session is None:
                return
        self._maintain_mlto_theses_for_session(
            session=session,
            market_summary=market_summary,
            analyst_reports=(
                getattr(session, "analyst_reports", None)
                if isinstance(getattr(session, "analyst_reports", None), dict)
                else {}
            ),
            mode=_trade_mode,
            portfolio=_portfolio,
            symbols_batch=_sym_one,
            run_mid=_run_mid,
            run_long=_run_long,
            # [2026-07-31] 长线开仓/thesis 必须 deep_context：注入 4h/1d/1w OHLCV。
            # 禁止 light_context=True（只会塞一行 EMA/RSI 标量，AI 看不到真实 K 线）。
            light_context=False,
        )
        _executed_tiers = [t for t, ran in (("mid", _run_mid), ("long", _run_long)) if ran]
        # 注意：独立循环不调用 mark_tier_run（避免主循环永远认为 long 不到期）。
        # [2026-07-31] 双入口收敛：maintain 与 execute_mlto_lane 共用 mlto_handled_keys
        # 原子占位，同一 symbol:tier 本轮只跑一次 LLM/开仓。
        # 长 LLM 段结束：开全新连接（不复用可能已被掐断的旧连接）做后续主动退出与落库，
        # merge 把 _maintain 期间在内存累加的 event_log 等改动合并回新会话再提交。
        db = SessionLocal()
        try:
            session = db.merge(session)
        except Exception as _merge_err:
            logger.debug("[MidLongAgent独立] session merge 跳过: %s", _merge_err)
            session = db.query(FullAutoSession).filter(
                FullAutoSession.session_id == session_id
            ).first()
        # 阶段二 B2：中长线主动退出。论点破坏（多周期 bias 强反向）时主动平仓，
        # 不再死等 max_hold_timeout。复用已算好的 orchestrator bias。
        # [P2-5 修复] 原实现仅 paper 模式自动执行（`if _trade_mode == "paper"`），
        # live 模式 bias_reversal / no-progress 主动退出缺位 → 实盘仓位开仓后
        # 只能死等 SL/TP。现在 live 同样走统一离场状态机仲裁（内部受
        # MIDLONG_ACTIVE_EXIT_ENABLED 总开关 + exit_state_machine 的 min_hold /
        # 微利减仓保护），对真实资金更负责。
        try:
            if session is not None:
                self._run_midlong_active_exit(db, session, market_summary)
        except Exception as _exit_err:
            logger.debug("[MidLongExit] 主动退出检查跳过: %s", _exit_err)
        self._safe_commit(db, "midlong_independent_tick", session=session)
    finally:
        try:
            db.rollback()
        except Exception:
            pass
        db.close()
