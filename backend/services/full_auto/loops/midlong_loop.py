"""
中长线独立循环 — midlong_loop 拆分。

[M12 2026-08-21] 本循环负责 mid/long 两个 tier 的独立 tick（不再只跑 long）。
[2026-09-05 LLM 主脑] mid/long 新开仓唯一走 run_midlong_brain_batch →
execute_midlong_open(source=mlto)。因子路由 / long_trend_v2 / E1 默认关闭，
只产证据。本循环 ~45s 哨兵盯盘（止损/失效价/should_close），论题按行情时钟重问。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from backend.services.full_auto_trading_service import FullAutoTradingService

logger = logging.getLogger(__name__)


def plan_mid_scan_batch(
    *,
    mid_universe: List[str],
    ai_scan: List[str],
    fixed_mid: set,
    batch_n: int,
    ai_slots: int,
    fix_cursor: int = 0,
    ai_cursor: int = 0,
) -> dict:
    """[2026-09-16 调研轮8] 把每 tick 的中线扫描批次拆成「固定币配额 + AI 配额」。

    ## 为什么必须拆（修「AI 选了不下单」）

    原实现 `mid_universe = 固定 ∪ AI`，两者**共用同一个滚动游标**、每 tick 取 batch 个。
    2026-09-04 的复盘记录：AI 那 3 个名额会独占整整一轮，把固定币挤到下一轮，且
    AVAX/LINK 在 active 取不到 K 线 → 约 1/4 扫描算力空转。当时的处置是把
    `MIDLONG_MID_AI_CANDIDATES_ENABLED` 关成 false —— 副作用是 **AI 选币彻底不参与
    扫描**（`get_ai_mid_candidates_for_session` 明明返回 FET/AMAT，却没有 tick 去看它们），
    这正是用户观察到的「AI 选币选了不下单」。

    拆配额后：
      * 固定币走原游标，占 `batch_n - ai_slots` 个名额（轮转不被 AI 挤占）；
      * AI 币走**独立游标**，占 `ai_slots` 个名额（默认 1，0=不扫 AI=旧行为）；
      * AI 名单里与固定币重名的会被剔除（固定币本就每轮都在扫，无需重复）。

    返回 `{"batch": [...], "next_fix_cursor": int, "next_ai_cursor": int}`。
    纯函数：无 DB、无副作用，便于契约测试。
    """
    _ai_slots = max(0, int(ai_slots or 0))
    _batch_n = max(1, int(batch_n or 1))
    _fixed_set = {str(s).upper() for s in (fixed_mid or set()) if s}
    _ai_list: List[str] = []
    for s in (ai_scan or []):
        _u = str(s or "").upper()
        if _u and _u not in _fixed_set and _u not in _ai_list:
            _ai_list.append(_u)
    _fix_list: List[str] = []
    for s in (mid_universe or []):
        _u = str(s or "").upper()
        if _u and _u not in _ai_list and _u not in _fix_list:
            _fix_list.append(_u)

    _n_ai = min(_ai_slots, len(_ai_list))
    _n_fix = max(0, min(_batch_n - _n_ai, len(_fix_list)))
    batch: List[str] = []
    if _fix_list and _n_fix:
        batch += [_fix_list[(int(fix_cursor) + i) % len(_fix_list)] for i in range(_n_fix)]
        next_fix = (int(fix_cursor) + _n_fix) % len(_fix_list)
    else:
        next_fix = int(fix_cursor) % max(1, len(_fix_list))
    if _ai_list and _n_ai:
        batch += [_ai_list[(int(ai_cursor) + i) % len(_ai_list)] for i in range(_n_ai)]
        next_ai = (int(ai_cursor) + _n_ai) % len(_ai_list)
    else:
        next_ai = int(ai_cursor) % max(1, len(_ai_list))
    return {"batch": batch, "next_fix_cursor": next_fix, "next_ai_cursor": next_ai}


def run_midlong_independent(svc: "FullAutoTradingService", session_id: str, tick: int) -> None:
    """中长线 tick：市场扫描 + LLM 论题批次 + 持仓主动退出。"""
    self = svc
    # [C1] 中长线循环是 APScheduler 后台循环,不在 HTTP 请求上下文。设 system_identity
    # 覆盖整轮(含下方两次 db = SessionLocal() 重连模式 + _run_midlong_active_exit)。
    from backend.core.tenant import set_system_identity
    set_system_identity()
    from backend.database.connection import SessionLocal
    from backend.database.connection import release_idle_txn as _release_txn
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
        # [2026-09-07] 论题 should_close / bias / no_progress：每 tick 【开头】先哨兵。
        # 旧口径只在 due 为空或长 LLM 结束才跑 → 主脑写 should_close 后要等整轮
        # brain batch（可达数分钟）才平仓（BTC 4624 实证干挂 ~30min）。
        try:
            _exit_ms0 = (
                session.last_market_summary
                if isinstance(session.last_market_summary, dict)
                else {}
            )
            _release_txn(db, where="midlong_loop.pre_tick_exit")
            self._run_midlong_active_exit(db, session, _exit_ms0)
            self._safe_commit(db, "midlong_pre_tick_active_exit", session=session)
        except Exception as _pre_exit_err:
            logger.debug("[MidLongExit] tick 开头主动退出跳过: %s", _pre_exit_err)

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
            # 开头已跑过 active_exit；此处只需落库返回
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
        # [源头切断] 长线只用固定交易对,绝不含 auto-coin / 历史 AI 选币
        from backend.services.auto_coin_selector import (
            get_ai_mid_candidates_for_session,
            get_fixed_symbols_for_session,
            sanitize_fixed_symbols_column,
        )
        _session_id = getattr(session, "session_id", None)
        if _session_id:
            try:
                _san = sanitize_fixed_symbols_column(_session_id, db)
                if _san.get("removed"):
                    logger.warning(
                        "[MidLongAgent独立] 清洗伪固定AI币 session=%s removed=%s kept=%s",
                        _session_id, _san.get("removed"), _san.get("kept"),
                    )
                    db.refresh(session)
            except Exception as _san_err:
                logger.debug("[MidLongAgent独立] sanitize_fixed skip: %s", _san_err)
        _fixed_long = (
            get_fixed_symbols_for_session(_session_id, db, tier="long")
            if _session_id else set()
        )
        _fixed_mid = (
            get_fixed_symbols_for_session(_session_id, db, tier="mid")
            if _session_id else set()
        )
        # [2026-08-10 问题三] AI 中线候选：只读消费平台看板 midlong approve，
        # 仅走 mid lane（与长线白名单正交、互不污染）；候选为空时
        # _run_mid 仍可因固定中线币而继续。
        # [2026-09-04] MIDLONG_MID_AI_CANDIDATES_ENABLED=false 时中线只用固定币，
        # 不再让 AI 候选占扫描名额（见 settings 同名开关的实测依据）。
        from backend.config.settings import (
            MIDLONG_MID_AI_CANDIDATES_ENABLED as _AI_MID_ON,
        )
        _ai_mid: List[str] = []
        if _session_id and _AI_MID_ON:
            try:
                _ai_mid = list(get_ai_mid_candidates_for_session(_session_id, db=db) or [])
            except Exception as _ai_err:
                logger.debug("[MidLongAgent独立] AI 中线候选查询跳过: %s", _ai_err)
        # 续管：已开 mid 仓即使不在候选表，也并入本轮 mid 扫描/管仓集合
        _ai_mid_hold: List[str] = []
        try:
            _acct = self._get_trading_account_id(db, session) if hasattr(self, "_get_trading_account_id") else None
            if not _acct:
                _acct = getattr(session, "paper_account_id", None) or getattr(session, "account_id", None)
            if _acct:
                from backend.services.full_auto.midlong_position_manager import (
                    _open_midlong_positions as _omp,
                )
                for _p in _omp(db, int(_acct)) or []:
                    if str(_p.get("timeframe_tier") or "").lower() == "mid" or str(
                        _p.get("trade_nature") or ""
                    ).lower() == "swing":
                        _su = str(_p.get("symbol") or "").upper()
                        if _su and _su not in _ai_mid and _su not in _ai_mid_hold:
                            _ai_mid_hold.append(_su)
        except Exception as _hold_err:
            logger.debug("[MidLongAgent独立] mid 持仓续管并入跳过: %s", _hold_err)
        _ai_mid_scan = list(dict.fromkeys(list(_ai_mid) + list(_ai_mid_hold)))
        # [2026-09-08] 长线引入 AI 选币：长线宇宙 = long 固定 ∪ AI长线候选 ∪ 长线续管持仓
        # （中线宇宙 = mid 固定 ∪ AI中线 ∪ 续管持仓，不变）
        _ai_long: List[str] = []
        if _session_id:
            try:
                from backend.config.settings import (
                    MIDLONG_LONG_AI_CANDIDATES_ENABLED as _AI_LONG_ON,
                )
                if _AI_LONG_ON:
                    from backend.services.auto_coin_selector import (
                        get_ai_long_candidates_for_session,
                    )
                    _ai_long = list(get_ai_long_candidates_for_session(_session_id, db=db) or [])
            except Exception as _ai_long_err:
                logger.debug("[MidLongAgent独立] AI 长线候选查询跳过: %s", _ai_long_err)
        # 长线续管：已开 long 仓即使不在固定/候选，也并入本轮长线扫描/管仓集合
        _ai_long_hold: List[str] = []
        try:
            from backend.services.full_auto.midlong_position_manager import (
                _open_midlong_positions as _omp_l,
            )
            _acct_l = getattr(session, "paper_account_id", None) or getattr(session, "account_id", None)
            if _acct_l:
                for _p in _omp_l(db, int(_acct_l)) or []:
                    if str(_p.get("timeframe_tier") or "").lower() == "long":
                        _su = str(_p.get("symbol") or "").upper()
                        if _su and _su not in _fixed_long and _su not in _ai_long_hold:
                            _ai_long_hold.append(_su)
        except Exception as _hold_l_err:
            logger.debug("[MidLongAgent独立] long 持仓续管并入跳过: %s", _hold_l_err)
        symbols = list(dict.fromkeys(list(_fixed_long) + list(_ai_long) + list(_ai_long_hold)))
        _mid_universe = list(dict.fromkeys(list(_fixed_mid) + list(_ai_mid_scan)))
        if not symbols:
            logger.warning(
                "[MidLongAgent独立] tick#%s 长线白名单为空，跳过入场分析（不回退全量交易对）",
                tick,
            )
        logger.info(
            f"[MidLongAgent独立] tick#{tick} 固定long={list(_fixed_long)} ai_long={_ai_long} "
            f"long_hold={_ai_long_hold} long_universe={symbols} 固定mid={list(_fixed_mid)} "
            f"ai_mid={_ai_mid} hold={_ai_mid_hold} mid_universe={_mid_universe}"
        )
        market_summary = session.last_market_summary if isinstance(session.last_market_summary, dict) else {}
        # 市场扫描覆盖 long(fixed) + mid(固定∪AI∪续管)
        _scan_syms = list(dict.fromkeys(list(symbols) + list(_mid_universe)))
        # [2026-09-02 挂事务修复] 上面全是 SELECT（session/固定币/候选/持仓），到此先结束
        # 读事务：_scan_markets/_ensure_market_prices 是交易所网络调用，动辄十几秒，
        # LeakGuard 追踪点名本函数 39 行开启的事务在此期间 idle-in-transaction。
        _release_txn(db, where="midlong_loop.pre_scan")
        try:
            fresh = self._scan_markets(db, _scan_syms)
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
                        _merged = {**_base, **_entry}
                        # [2026-09-07] 深合并不会删掉旧 data_stale；扫描成功必须清掉，
                        # 否则 VIRTUAL 等币一旦被 bootstrap 标 stale 就永久「跳过编排器」。
                        if float(_merged.get("current_price") or 0) > 0 and _merged.get(
                            "data_reliable", True
                        ):
                            _merged["data_stale"] = False
                        market_summary[_sym] = _merged
                    else:
                        market_summary[_sym] = dict(_entry) if isinstance(_entry, dict) else _entry
                session.last_market_summary = market_summary
                # [2026-09-02 挂事务修复] 原为 db.flush()：UPDATE full_auto_sessions 后行锁
                # 一直持到下方 267 行 midlong_pre_llm 提交，中间 _ensure_market_prices /
                # _build_portfolio_for_agents 还有网络与查询。这里直接提交（下方本就要提交，
                # 只是把窗口缩短；expire_on_commit=False，session 对象继续可用）。
                self._safe_commit(db, "midlong_scan_persist", session=session)
        except Exception as _scan_err:
            logger.debug("[MidLongAgent独立] 市场扫描跳过: %s", _scan_err)
        if _scan_syms:
            self._ensure_market_prices(market_summary, _scan_syms)
        # 编排器结果在 OrchBG 缓存里，合并进 market_summary（含 XPL 等非 session.symbols 币）
        _hold_syms = {str(s).upper() for s in (_ai_mid_hold or []) if s}
        for _sym in _scan_syms:
            _sym_u = str(_sym or "").upper()
            _sym_data = market_summary.get(_sym) or {}
            _stale = (
                not _sym_data.get("data_reliable", True) or _sym_data.get("data_stale")
            )
            # 持仓续管币：即使 stale 也合并编排器（管仓/空头下行证据依赖 mid_bias）
            if _stale and _sym_u not in _hold_syms:
                logger.info(
                    "[MidLongAgent独立] %s 数据不可靠/过期，跳过编排器合并",
                    _sym,
                )
                continue
            if _stale and _sym_u in _hold_syms:
                logger.info(
                    "[MidLongAgent独立] %s 持仓续管：数据标过期仍合并编排器",
                    _sym,
                )
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
        # 中线：固定交易对始终在 + AI中线≤3 + 续管；不再只扫 AI 候选
        _run_mid = ("mid" in due) and bool(_mid_universe)
        _run_long = "long" in due
        # [2026-09-07] LLM 日内波段车道：short tier 进入调度轮转（INTRADAY_LLM_ENABLED 门控）
        _run_short = "short" in due
        # 分游标：long 只滚固定币；mid 滚固定∪AI∪续管
        _sym_one: List[str] = []
        if _run_long and symbols:
            _long_cur = int(self._midlong_symbol_cursor.get(session_id, 0) or 0)
            _n_long = min(_batch_n, len(symbols))
            _long_batch = [symbols[(_long_cur + i) % len(symbols)] for i in range(_n_long)]
            _sym_one.extend(_long_batch)
            self._midlong_symbol_cursor[session_id] = (_long_cur + _n_long) % len(symbols)
        if _run_mid and _mid_universe:
            if not hasattr(self, "_midlong_ai_mid_cursor"):
                self._midlong_ai_mid_cursor = {}
            if not hasattr(self, "_midlong_ai_cand_cursor"):
                self._midlong_ai_cand_cursor = {}
            try:
                from backend.config.settings import MIDLONG_MID_AI_SCAN_SLOTS as _ai_slots_cfg
                _ai_slots = max(0, int(_ai_slots_cfg if _ai_slots_cfg is not None else 1))
            except Exception:
                _ai_slots = 1
            _plan = plan_mid_scan_batch(
                mid_universe=_mid_universe,
                ai_scan=_ai_mid_scan,
                fixed_mid=_fixed_mid,
                batch_n=_batch_n,
                ai_slots=_ai_slots,
                fix_cursor=int(self._midlong_ai_mid_cursor.get(session_id, 0) or 0),
                ai_cursor=int(self._midlong_ai_cand_cursor.get(session_id, 0) or 0),
            )
            self._midlong_ai_mid_cursor[session_id] = _plan["next_fix_cursor"]
            self._midlong_ai_cand_cursor[session_id] = _plan["next_ai_cursor"]
            _sym_one.extend(_plan["batch"])
        _sym_one = list(dict.fromkeys(_sym_one))
        _trade_mode = (getattr(session, "trading_mode", None) or "paper").strip().lower()
        logger.info(
            "[MidLongAgent独立] batch=%s mid=%s long=%s scan_n=%d ai_mid=%s mid_u=%s",
            _sym_one, _run_mid, _run_long, len(_sym_one), _ai_mid, _mid_universe,
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
        # [2026-09-02 挂事务修复] merge/重查 session 开启了新事务，而下方 _maintain 跑数百秒
        # LLM（开仓走独立 _SwingDB，不用本 db）——LeakGuard 追踪 18 分钟点名本处 7 次。
        _release_txn(db, where="midlong_loop.pre_maintain")
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
            mid_universe=_mid_universe,
            run_mid=_run_mid,
            run_long=_run_long,
            run_short=_run_short,
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
