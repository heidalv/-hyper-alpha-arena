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
    # [2026-09-01 F34] 长线车道活动快照写入（前端 tier-activity "固定长线" 列）
    persist_tcp_snapshot: Callable = field(repr=False, default=lambda *a, **k: False)
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
        # [2026-09-01 F34] 长线车道快照（tier-activity 展示）
        persist_tcp_snapshot=getattr(svc, "_persist_tcp_snapshot", None) or (lambda *a, **k: False),
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
    # [2026-08-22 M0-6] 跨层防护：MLTO invalidation 只允许平 mid/long 性质仓
    # （swing/trend_follow/position），禁止平 scalp 仓；且必须传 position_id
    # 精确定位，避免同 symbol 多腿时"平错腿"。
    db_side: Optional[str] = None
    db_pos_id: Optional[int] = None
    db_nature: Optional[str] = None
    if db is not None:
        try:
            from backend.database.models import PaperPosition
            pos = db.query(PaperPosition).filter(
                PaperPosition.account_id == acct_id,
                PaperPosition.symbol == sym_u,
                PaperPosition.status == "open",
                PaperPosition.trade_nature.in_(("swing", "trend_follow", "position")),
            ).order_by(PaperPosition.id.desc()).first()
            if pos is not None:
                db_side = str(pos.side or "").lower()
                db_pos_id = pos.id
                db_nature = pos.trade_nature
        except Exception as _db_err:
            logger.debug("[MLTO] close 查 DB side 失败 %s, 退回 thesis 兜底: %s", sym_u, _db_err)

    closed_any = False
    if db_side in ("long", "short"):
        closed_any = paper_engine.close_position(
            db, acct_id, sym_u, db_side, reason=reason[:120],
            position_id=db_pos_id, trade_nature=db_nature,
        ) is not None
        return closed_any

    # ── 兜底1：DB 查不到（无中长线持仓或查询失败）→ 用 thesis.direction ──
    direction = str(getattr(thesis, "direction", "") or "").lower()
    if direction == "long":
        closed_any = paper_engine.close_position(
            db, acct_id, sym_u, "long", reason=reason[:120],
            trade_nature="swing",
        ) is not None
    elif direction == "short":
        closed_any = paper_engine.close_position(
            db, acct_id, sym_u, "short", reason=reason[:120],
            trade_nature="swing",
        ) is not None
    else:
        # 兜底2：方向未知 → 尝试两边（只会平掉实际存在的那一边）
        for _side in ("long", "short"):
            try:
                if paper_engine.close_position(
                    db, acct_id, sym_u, _side, reason=reason[:120],
                    trade_nature="swing",
                ) is not None:
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
    run_short: bool = False,
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

    # [2026-09-05] 中线 SwingAgent 已废弃。主脑开启时走 midlong_thesis；
    # 因子路由仅在 MIDLONG_MID_VIA_FACTOR_ROUTE 且脑关闭时自开（默认关）。
    if run_mid:
        # [2026-09-05] LLM 主脑：中线新开只走 midlong_thesis；因子路由只产证据。
        from backend.config.settings import MIDLONG_MID_VIA_FACTOR_ROUTE as _FR
        from backend.config.settings import midlong_brain_enabled as _brain_on
        _mid_syms = list(dict.fromkeys(
            [str(s).upper() for s in (mid_universe or [])]
            or [str(s).upper() for s in symbols]
        ))
        if _brain_on() and _mid_syms:
            try:
                # [2026-09-07 解耦] 异步派发：LLM 慢分析不阻塞 45s 因子/哨兵循环
                from backend.services.mlto.brain import run_midlong_brain_batch_async
                run_midlong_brain_batch_async(
                    host=host,
                    session=session,
                    symbols=_mid_syms,
                    tier="mid",
                    market_summary=market_summary,
                    trading_mode=_trade_mode,
                    reserve_key=_reserve_key,
                )
            except Exception as _br_err:
                logger.warning("[MidLongBrain] 中线批次异常: %s", _br_err, exc_info=True)
            # [M5 2026-09-14] A/B 车道：脑开启时因子路由也并行自开（paper），
            # entry_source=factor_route 独立记账，月末按车道对账。开关
            # MIDLONG_MID_FACTOR_ROUTE_AB（默认 true）；false=回滚旧行为。
            try:
                import os as _os_ab
                _ab_on = (_os_ab.getenv("MIDLONG_MID_FACTOR_ROUTE_AB", "true") or "true"
                          ).strip().lower() in ("1", "true", "yes", "on")
                if _ab_on and (_trade_mode or "paper").strip().lower() == "paper" and _FR:
                    for _m in _mid_syms:
                        if not _reserve_key(f"{_m}:mid:ab"):
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
                                "[FactorRouteAB] %s action=%s score=%s opened=%s gate=%s | %s",
                                _m, _fr_dec.get("action"), _fr_dec.get("score"),
                                _fr_dec.get("opened"), _fr_dec.get("gate"),
                                (_fr_dec.get("reason") or "")[:110],
                            )
                        except Exception as _fr_err:
                            logger.warning("[FactorRouteAB] %s 决策异常: %s", _m, _fr_err, exc_info=True)
            except Exception as _ab_err:
                logger.warning("[FactorRouteAB] A/B 车道跳过: %s", _ab_err)

            # ── [轮109 2026-09-19] 因子路由**影子档**：只决策、只记日志、不开仓 ──
            # 为什么要有这一档：A/B 实盘证据已经给出判决 ——
            #   `entry_source=factor_route` 34 笔：毛利 −83.33、手续费 20.22、**净 −103.55**
            #   （笔均净 −3.05）；同期 `mlto` 28 笔 **净 +98.52**（笔均 +3.52）。
            #   中线整体 62 笔：毛利 +31.78 − 费 36.81 = **净 −5.03** ⇒ 这一条路径就是全部亏损来源。
            # 所以把 `MIDLONG_MID_VIA_FACTOR_ROUTE` 关掉止血；但**证据不能断**，
            # 否则以后无法回答"关对了没有/要不要再开"。`MIDLONG_MID_FACTOR_ROUTE_SHADOW`
            # （默认 true）在 VIA=false 时继续逐币决策 + 记 `[FactorRouteShadow]`，
            # 只把 `opened` 去掉。要彻底静默：SHADOW=false。
            try:
                import os as _os_sh
                _shadow_on = (_os_sh.getenv("MIDLONG_MID_FACTOR_ROUTE_SHADOW", "true") or "true"
                              ).strip().lower() in ("1", "true", "yes", "on")
                if (not _FR) and _ab_on and _shadow_on and _mid_syms and (
                        (_trade_mode or "paper").strip().lower() == "paper"):
                    from backend.services.factor_engine.midlong_factor_route import (
                        factor_route_decide as _frd_sh,
                    )
                    for _m in _mid_syms:
                        try:
                            _sh_dec = _frd_sh(_m, market_summary, trading_mode=_trade_mode)
                            logger.info(
                                "[FactorRouteShadow] %s action=%s score=%s gate=%s | %s",
                                _m, _sh_dec.get("action"), _sh_dec.get("score"),
                                _sh_dec.get("gate") or _sh_dec.get("reason"),
                                (_sh_dec.get("reason") or "")[:110],
                            )
                        except Exception as _sh_err:
                            logger.debug("[FactorRouteShadow] %s 决策异常: %s", _m, _sh_err)
            except Exception as _sh_outer:
                logger.debug("[FactorRouteShadow] 影子档跳过: %s", _sh_outer)
        elif _FR and _mid_syms:
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
            for _s in symbols:
                _reserve_key(f"{str(_s).upper()}:mid")

    # ── [2026-09-07] LLM 日内波段车道（short tier）──
    # 旧因子 scalp 已判死（90天2756笔胜率25%）；本车道复用 LLM 主脑论题机制，
    # 1h 级别、持仓 4-12h、每币冷却 4h。宇宙 = 固定 short 币；为空时回退 mid 固定币
    # （已获批的中线币是合理的日内候选），并在日志显式标注。
    if run_short:
        try:
            from backend.config.settings import midlong_brain_enabled as _brain_short_on
            from backend.services.auto_coin_selector import (
                get_fixed_symbols_for_session as _gfs_short,
            )
            _short_syms = []
            try:
                _short_syms = [str(s).upper() for s in (_gfs_short(session_id, db=None, tier="short") or []) if s]
            except Exception as _gs_err:
                logger.debug("[MidLongBrain] short 固定币读取跳过: %s", _gs_err)
            if not _short_syms:
                _short_syms = list(_mid_syms or [])
                if _short_syms:
                    logger.info("[MidLongBrain] short 固定币为空，回退 mid 宇宙: %s", _short_syms)
            if _brain_short_on() and _short_syms:
                # [2026-09-07 解耦] 异步派发
                from backend.services.mlto.brain import run_midlong_brain_batch_async as _short_brain_async
                _short_brain_async(
                    host=host,
                    session=session,
                    symbols=_short_syms,
                    tier="short",
                    market_summary=market_summary,
                    trading_mode=_trade_mode,
                    reserve_key=_reserve_key,
                )
        except Exception as _sb_err:
            logger.warning("[MidLongBrain] 日内波段(short)批次异常: %s", _sb_err, exc_info=True)

    # 长线论题必须在 TrendAgent 占 key / 持仓 LLM 之前跑，否则本轮永远轮不到。
    _fixed_symbols_early: set = set()
    try:
        from backend.services.auto_coin_selector import get_fixed_symbols_for_session as _gfs_early
        _fixed_symbols_early = {
            str(s).upper() for s in (_gfs_early(session_id, tier="long") or []) if s
        }
    except Exception as _fs_err:
        logger.debug("[MidLongBrain] 长线固定币读取跳过: %s", _fs_err)
    try:
        from backend.config.settings import midlong_brain_enabled as _brain_long_now
        # [验收轮2 2026-09-14] E1 独占长车道时，脑不再派 tier=long 批次：
        # 新开在 place_order 收口处必被 E1 独占闸拒绝（仅记为提议），论题白烧
        # dual_call LLM（实测每 ~2-3 分钟一批 n=9，8 小时 0 成交）。镜像 F38f
        # 的空头提示策略——方向研判留给 E1 日任务，脑专注 mid/short 车道。
        _e1_exclusive = False
        try:
            from backend.services.trend_e1_engine import long_lane_exclusive as _e1_lle
            _e1_exclusive = bool(_e1_lle())
        except Exception:
            pass
        # [2026-09-18 撤销验收轮2的整段跳过] 用户裁决：分析不许省。脑长线批次恢复
        # 全频率派发（分析/论题/持仓管理视角照常产出）；下单仍被 E1 独占闸拒绝，
        # 交易权不变。频率/触发式调度如需调整必须先出数据方案经用户确认。
        if _brain_long_now() and run_long and _fixed_symbols_early:
            from backend.services.mlto.brain import run_midlong_brain_batch_async as _long_brain_async
            _long_brain_async(
                host=host,
                session=session,
                symbols=sorted(_fixed_symbols_early),
                tier="long",
                market_summary=market_summary,
                trading_mode=_trade_mode,
                reserve_key=_reserve_key,
            )
        elif _e1_exclusive and run_long:
            logger.info("[MidLongBrain] E1 独占长车道（脑长线批次仍派发：分析/论题产出，交易权在 E1）")
    except Exception as _lb_err:
        logger.warning("[MidLongBrain] 长线批次异常: %s", _lb_err, exc_info=True)

    # SwingDB 句柄曾供 _swing_one 使用；保留 import 兼容下游 _trend_one 的 DB 工厂。
    from backend.database.connection import SessionLocal as _SwingDB
    from backend.database.connection import release_idle_txn as _release_txn
    _swing_db = None  # type: ignore[assignment]

    # ═══ 长线 TrendAgent 独立决策（并行 LLM）═══
    # TrendAgent 路径在主脑开启时 brain_owns_entry（只分析/管仓，不开新仓）。
    # 新开唯一走上方 run_midlong_brain_batch。
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
                # [Phase 5] 模式切换（§7.2）：该交易对已有未平仓【长线】仓位
                # → 进入模式 B 持仓管理分析（六维发展分析），不再重复做入场分析。
                # [2026-08-23 融合改造·用户决定] 同币允许 mid+long 并存：仅已有 mid（swing）
                # 仓时不再短路，继续走下方长线入场分析；开仓后的集中度由组合预算兜底。
                try:
                    from backend.services.full_auto.midlong_position_manager import (
                        has_open_position_of_nature as _has_nature_pos,
                        _open_midlong_positions as _open_ml_pos,
                        manage_position,
                    )
                    _mgmt_acct = getattr(session, "paper_account_id", None) or getattr(session, "account_id", None)
                    if _has_nature_pos(_db_t, _mgmt_acct, sym_u, "long"):
                        # 精确管理【long】仓（同币可能有并存 mid 仓，勿误管）
                        _long_pos = next(
                            (p for p in _open_ml_pos(_db_t, _mgmt_acct)
                             if str(p.get("symbol") or "").upper() == sym_u
                             and (str(p.get("trade_nature") or "").lower() in ("trend_follow", "position")
                                  or str(p.get("timeframe_tier") or "").lower() == "long")),
                            {},
                        )
                        # [2026-09-02 挂事务修复] 持仓读完、进入 LLM 六维分析前结束只读事务，
                        # 否则连接在整个 LLM 调用期间 idle-in-transaction（LeakGuard 实测点名）。
                        _release_txn(_db_t, where="mlto_trend_one.manage")
                        _mgmt_dec = manage_position(
                            _db_t, host=host, session=session, account_id=_mgmt_acct,
                            symbol=sym_u, position=_long_pos,
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
                try:
                    from backend.config.settings import midlong_brain_enabled
                    if midlong_brain_enabled():
                        _release_txn(_db_t, where="mlto_trend_one.brain")
                        return (sym_u, "hold", 0, "neutral", "brain_owns_entry", "brain_owns_entry")
                except Exception:
                    pass
                # [2026-09-02 挂事务修复] 模式 A：互锁判定的读事务到此结束，下方 V2/LLM
                # 方向分析与开仓前不再带着空事务。
                _release_txn(_db_t, where="mlto_trend_one.analyze")
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
                # ── 融合仲裁（阶段1：long_trend_v2 × LLM thesis 否决）──
                # LLM thesis=standdown（strong bearish 且 conf>=0.6）→ 暂停新开；
                # 无 thesis / 弱反对 → L1 规则自决（fail-open）。
                try:
                    from backend.services.decision_fusion_arbiter import decide_long
                    _ms_ls = (market_summary or {}).get(sym_u) if isinstance(market_summary, dict) else {}
                    if not isinstance(_ms_ls, dict):
                        _ms_ls = {}
                    _orch_ls = _ms_ls.get("orchestrator") if isinstance(_ms_ls.get("orchestrator"), dict) else {}
                    _lb_raw = str(_orch_ls.get("long_bias") or "").strip().lower()
                    try:
                        _lb_conf = float(_orch_ls.get("long_confidence") or 0)
                    except Exception:
                        _lb_conf = 0.0
                    _thesis_state = None
                    # [2026-08-25 转正] 优先用真实 long thesis（影子产出）：short 且 conv>=60 → standdown
                    try:
                        from backend.services.mlto.thesis_store import get as _thl_get
                        _thl = _thl_get(str(getattr(session, "session_id", "") or ""), sym_u, "long")
                        if _thl is not None and str(getattr(_thl, "direction", "") or "").lower() == "short"                                 and int(getattr(_thl, "llm_conviction", 0) or 0) >= 60:
                            _thesis_state = "standdown"
                            logger.info("[FusionLong] %s LLM thesis standdown(conv=%d)",
                                        sym_u, getattr(_thl, "llm_conviction", 0))
                    except Exception:
                        pass
                    if _thesis_state is None and _lb_raw in ("bearish", "short") and _lb_conf >= 0.6:
                        _thesis_state = "standdown"
                    # ── R2 风控禁开（阶段3）──
                    try:
                        if os.getenv("FUSION_RISK_EVENT_BAN", "true").strip().lower() not in ("0", "false", "off"):
                            from backend.services.symbol_penalty import is_risk_banned as _risk_banned_l
                            if _risk_banned_l(sym_u):
                                if _v2_entry is not None:
                                    _v2_entry["should_open"] = False
                                    _v2_entry["hold_reason"] = "fusion_risk_ban"
                                logger.info("[FusionLong] %s 24h 风控禁开（单笔已实现亏损>1.5%%权益）", sym_u)
                                host.append_event(
                                    session, "fusion_risk_ban",
                                    f"⛔ 风控禁开 {sym_u}: 单笔已实现亏损>1.5%%权益（禁开期自动恢复）",
                                )
                    except Exception as _rb_err_l:
                        logger.debug("[FusionLong] %s 风控禁开检查失败: %s", sym_u, _rb_err_l)

                    _fusion_long = decide_long(
                        thesis_state=_thesis_state,
                        l1_state="up" if (_v2_entry or {}).get("should_open") else "sideways",
                    )
                    if not _fusion_long.allowed and _v2_entry is not None:
                        if _fusion_long.source == "llm":
                            # 真 thesis 否决（strong bearish + conf>=0.6）→ 暂停新开
                            _v2_entry["should_open"] = False
                            _v2_entry["hold_reason"] = "fusion_thesis_standdown"
                            _v2_entry["fusion"] = _fusion_long.to_dict()
                            logger.info(
                                "[FusionLong] %s thesis 否决: %s (long_bias=%s conf=%.2f)",
                                sym_u, _fusion_long.reason, _lb_raw, _lb_conf,
                            )
                        # L1=sideways 的 hold 属规则自决（source=factor），不改 _v2_entry
                except Exception as _fl_err:
                    logger.debug("[TrendAgent][V2] 融合仲裁跳过: %s", _fl_err)
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
                        # [A4] 首仓比例由 entry_signal 下发（默认 50% 试探仓）
                        "size_hint_mult": float(_v2_entry.get("size_hint_mult") or 1.0),
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
                        account_id=(
                            getattr(session, "paper_account_id", None)
                            or getattr(session, "account_id", None)
                        ),
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

                # [2026-09-01 F34] 长线车道活动快照：trend 车道此前不写
                # DecisionSnapshot → 前端 tier-activity 的"固定长线"列恒为 0，
                # 用户观感"长线不分析"（实际 TrendAgent 每轮都在评，只是没落快照）。
                # 补写 tier=long 快照：hold 带原因，buy/sell 带方向，读侧 600s
                # 窗口去重防刷屏。
                try:
                    host.persist_tcp_snapshot(
                        session,
                        symbol=sym_u,
                        tier="long",
                        action=_trend_action,
                        confidence=float(_trend_score),
                        reasoning=(
                            (str(_trend_result.get("hold_reason") or "trend_hold"))
                            if _trend_action == "hold"
                            else f"trend_dir={_trend_dir} score={_trend_score}"
                        ),
                        source_lane="long_lane",
                        executed=False,
                    )
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
                    # [§53.6 修复] 与本文件其它处的惯例一致（第 91/360/709 行）：
                    # 仅取 paper_account_id 在 None 时会让审计落库退化为 account_id=0，
                    # 实测 ai_decision_logs 有 9.4%（7,424/79,218）行因此无法归因到账户。
                    account_id=(
                        getattr(session, "paper_account_id", None)
                        or getattr(session, "account_id", None)
                    ),
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
            try:
                from backend.config.settings import midlong_brain_enabled as _brain_owns
                if _brain_owns():
                    # 主脑已占用入场。这里只把已有长线仓送去持仓管理，绝不占 SYM:long。
                    from backend.services.full_auto.midlong_position_manager import (
                        has_open_position_of_nature as _has_long_pos,
                    )
                    _acct_l = getattr(session, "paper_account_id", None) or getattr(
                        session, "account_id", None
                    )
                    _chk = _SwingDB()
                    try:
                        if _has_long_pos(_chk, _acct_l, _su, "long"):
                            _long_targets.append(_su)
                    finally:
                        _chk.close()
                    continue
            except Exception as _bo_err:
                logger.debug("[MLTO] 主脑长线占位检查跳过: %s", _bo_err)
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
            has_open_position_of_nature as _has_midlong_pos,
            manage_position as _manage_pos,
        )
        _fixed_long_now = set(
            str(x).upper() for x in (_gfs_long(session_id, tier="long") or [])
        )
        _mgmt_acct = (
            getattr(session, "paper_account_id", None)
            or getattr(session, "account_id", None)
        )
        # [2026-09-07] 管仓目标 = 本批扫描币 ∪ 全部未平 mid 仓。
        # 旧逻辑只扫 symbols_batch → 有仓但不在 batch 的币永远进不了
        # manage_position（should_close 干挂）。
        _mid_manage_targets = [
            str(s).upper() for s in symbols
            if str(s).upper() not in _fixed_long_now
        ]
        if _mgmt_acct:
            try:
                from backend.services.full_auto.midlong_position_manager import (
                    _open_midlong_positions as _omp_all,
                )
                _db_hold = _SwingDB()
                try:
                    for _hp in _omp_all(_db_hold, int(_mgmt_acct)) or []:
                        if str(_hp.get("timeframe_tier") or "").lower() == "mid" or str(
                            _hp.get("trade_nature") or ""
                        ).lower() == "swing":
                            _hs = str(_hp.get("symbol") or "").upper()
                            if _hs and _hs not in _fixed_long_now and _hs not in _mid_manage_targets:
                                _mid_manage_targets.append(_hs)
                finally:
                    try:
                        _db_hold.close()
                    except Exception:
                        pass
            except Exception as _hold_m_err:
                logger.debug("[MidLong] 中线持仓并入管仓目标跳过: %s", _hold_m_err)
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
                    # [M2 2026-08-21] 中线管理只针对 mid 组持仓（long 仓由
                    # 长线链路管理，不再被这里当 mid 仓重复接管）
                    if not _has_midlong_pos(_db_m, _mgmt_acct, sym_u, "mid"):
                        return None
                    # [2026-09-02 挂事务修复] 同 _trend_one：LLM 管理分析前结束只读事务
                    _release_txn(_db_m, where="mlto_mid_manage_one")
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
        # [2026-09-05] 旧 run_mlto_tick / _thesis_llm_one 已下线。
        # 中长线新开只走 run_midlong_brain_batch；下面仍注入指标供主脑饲料。
        _ana_db = None
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

        # [2026-09-05] 长线入场改挂 LLM 主脑。旧 run_mlto_tick / 空 _thesis_futs /
        # thesis_shadow 不再是 Writer。
        try:
            from backend.config.settings import midlong_brain_enabled
            if not midlong_brain_enabled():
                from backend.services.mlto.thesis_shadow import run_shadow_batch
                _shadow_jobs = list(_thesis_jobs)
                if os.getenv("THESIS_SHADOW_INCLUDE_LONG", "false").strip().lower() in ("1", "true", "yes", "on"):
                    for _ls in sorted(set(_fixed_symbols or [])):
                        _shadow_jobs.append((_ls, "shadow", "long"))
                _shadow_done = run_shadow_batch(
                    session_id=session_id,
                    jobs=_shadow_jobs,
                    market_summary=market_summary,
                    analyst_reports=analyst_reports,
                    mode=_trade_mode,
                    session=session,
                )
                if _shadow_done:
                    logger.info("[ThesisShadow] 本轮处理 %d 个", _shadow_done)
        except Exception as _ts_err:
            logger.warning("[MLTO] 主脑/影子提交异常: %s", _ts_err)
        # [2026-09-05] 旧 run_mlto_tick / 空 _thesis_futs 消费循环已删除。
        # 中长线新开只走 run_midlong_brain_batch；handled 由 reserve_key 维护。
        host.mlto_handled_keys = handled
    except Exception as exc:
        logger.debug("[MLTO] session maintain skip: %s", exc)
    finally:
        if _ana_db is not None:
            try:
                _ana_db.close()
            except Exception:
                pass

