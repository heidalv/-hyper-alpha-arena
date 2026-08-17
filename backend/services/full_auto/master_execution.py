"""Master 总控决策执行 — 从 monolith _execute_master_decisions 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _hub_mode_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """[v6 S2-6] 决策快照注入 hub 模式/灰度权重（失败不改动，供灰度对比）。"""
    try:
        from backend.services.mlto.ai_governed_compare import snapshot_with_hub_mode
        return snapshot_with_hub_mode(snapshot)
    except Exception:
        return dict(snapshot or {})


@dataclass
class MasterExecutionHost:
    """monolith 状态与回调切片。"""

    market_scan_cache: Dict[str, Any]
    partial_close_tracker: Dict[str, Any]
    deferred_signals: Dict[str, Any]
    last_reduce_time: Dict[str, Any]
    position_last_decision_ts: Dict[str, Any]
    master_strat_cache: Dict[str, Any]
    nature_to_tier_map: Dict[str, str]
    position_min_decision_interval: Dict[str, int]
    deferred_max_retries: int
    sub_mgr: Any
    current_decision_tier: str = ""

    clear_master_strat_cache: Callable = field(repr=False, default=lambda: None)
    get_lock_profile: Callable = field(repr=False, default=lambda s: None)
    refresh_positions_local: Callable = field(repr=False, default=lambda *a, **k: (0, 0))
    expand_multi_tier_decisions: Callable = field(repr=False, default=lambda *a, **k: [])
    orchestrator_blocks_open: Callable = field(repr=False, default=lambda *a, **k: (False, ""))
    ensure_bound_strategy: Callable = field(repr=False, default=lambda *a, **k: None)
    load_strategy_by_id: Callable = field(repr=False, default=lambda *a, **k: None)
    execute_paper_trade: Callable = field(repr=False, default=lambda *a, **k: False)
    # [2026-08-17] execute_mlto_lane 字段已删：旧长线 LLM 分支已移除，Master 不再调用。
    try_execute_independent_agent_open: Callable = field(repr=False, default=lambda *a, **k: False)
    mark_master_decision_executed: Callable = field(repr=False, default=lambda *a, **k: None)
    backfill_dec_confidence_from_orch: Callable = field(repr=False, default=lambda *a, **k: 0)
    build_midlong_agent_envelope: Callable = field(repr=False, default=lambda *a, **k: {})
    midlong_persistence_allow: Callable = field(repr=False, default=lambda *a, **k: True)
    factor_veto_check: Callable = field(repr=False, default=lambda *a, **k: None)
    get_today_realized_pnl: Callable = field(repr=False, default=lambda *a, **k: 0.0)
    get_account_risk_score: Callable = field(repr=False, default=lambda *a, **k: 50.0)
    tiny_close_allowed_by_hardfact: Callable = field(repr=False, default=lambda *a, **k: (True, ""))
    paper_loss_locks_disabled: Callable = field(repr=False, default=lambda *a, **k: False)
    safe_commit: Callable = field(repr=False, default=lambda *a, **k: None)
    session_trading_mode: Callable = field(repr=False, default=lambda s: "paper")
    extract_ai_position_pct: Callable = field(repr=False, default=lambda *a, **k: None)
    resolve_alignment_scale: Callable = field(repr=False, default=lambda *a, **k: 1.0)
    resolve_decision_leverage: Callable = field(repr=False, default=lambda *a, **k: 10)
    calibrate_confidence: Callable = field(repr=False, default=lambda *a, **k: 50)
    ai_dynamic_position_pct: Callable = field(repr=False, default=lambda *a, **k: 0.0)
    apply_tdi_position_advice: Callable = field(repr=False, default=lambda *a, **k: None)
    get_direction_win_rate: Callable = field(repr=False, default=lambda *a, **k: None)
    get_symbol_direction_wr: Callable = field(repr=False, default=lambda *a, **k: (None, 0))
    log_pipeline_audit: Callable = field(repr=False, default=lambda *a, **k: None)
    validate_tp_sl_by_nature: Callable = field(repr=False, default=lambda *a, **k: (True, ""))
    is_reduce_cooldown_exempt: Callable = field(repr=False, default=lambda *a, **k: False)
    should_evaluate_position: Callable = field(repr=False, default=lambda *a, **k: True)
    record_position_decision: Callable = field(repr=False, default=lambda *a, **k: None)
    clear_deferred_signal: Callable = field(repr=False, default=lambda *a, **k: None)
    deferred_signal_key: Callable = field(repr=False, default=lambda *a, **k: "")
    clear_hold_timeout_queue_entry: Callable = field(repr=False, default=lambda *a, **k: None)
    event_scope_label: Callable = field(repr=False, default=lambda *a, **k: "")
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    defensive_reduce_cap: float = 0.25
    # [2026-07-11 修复] 从 monolith 迁出本文件时漏掉了这两个字段——下方 1636/2159/2194
    # 行都引用 host.TIER_PROTECTION / host.DEFAULT_PROTECTION（对应
    # FullAutoTradingService.TIER_PROTECTION 属性 + DEFAULT_PROTECTION 类属性），但
    # dataclass 从未声明过这两个字段，build_master_execution_host 也没有赋值。
    # 一旦某个仓位触发"新仓保护期"门控检查（action in close/reduce），必定抛
    # AttributeError，被 analyst_system_cycle 的外层 except 捕获后包装成
    # "分析师系统异常"/"AI 决策失败"，导致该 tick 整轮 AI 决策直接失败退出
    # （而不仅仅是这一个仓位被拦截）。这是用户报告的
    # "'MasterExecutionHost' object has no attribute 'TIER_PROTECTION'" 报错的根因。
    TIER_PROTECTION: Dict[str, Any] = field(default_factory=dict)
    DEFAULT_PROTECTION: Dict[str, Any] = field(
        default_factory=lambda: {"protect_min": 30, "emergency_pct": -5.0}
    )
    # [P3-修复] 模块级 execute_master_decisions 无法访问 self，此前用 getattr(self,...)
    # 抛 NameError：447 行被 try/except 吞掉导致 STRICT_DATA_GATE 数据门失效（fail-open），
    # 954 行导致 orch_snapshot_ts 恒 0。把 svc 侧状态透传给 host 供模块函数读取。
    last_unified_snapshot: Any = None
    last_orch_decisions_ts: float = 0.0
    # [P3-修复] 模块函数中另两处 getattr(self,...)（536/2867 行）同为 NameError：
    # scalp 去重失效、训练期禁开检查中断。一并透传。
    scalp_traded_this_tick: set = field(default_factory=set)
    training_allowed_symbols: set = field(default_factory=set)


def build_master_execution_host(svc) -> MasterExecutionHost:
    host = MasterExecutionHost(
        market_scan_cache=svc._market_scan_cache,
        partial_close_tracker=svc._partial_close_tracker,
        deferred_signals=svc._deferred_signals,
        last_reduce_time=svc._last_reduce_time,
        position_last_decision_ts=svc._position_last_decision_ts,
        master_strat_cache=getattr(svc, "_master_strat_cache", {}),
        nature_to_tier_map=svc._NATURE_TO_TIER_MAP,
        position_min_decision_interval=svc._POSITION_MIN_DECISION_INTERVAL,
        deferred_max_retries=getattr(svc, "_DEFERRED_MAX_RETRIES", 3),
        sub_mgr=svc._sub_mgr,
        defensive_reduce_cap=getattr(svc, "_defensive_reduce_cap", 0.25),
        clear_master_strat_cache=svc._clear_master_strat_cache,
        get_lock_profile=svc._get_lock_profile,
        refresh_positions_local=svc._refresh_positions_local,
        expand_multi_tier_decisions=svc._expand_multi_tier_decisions,
        orchestrator_blocks_open=svc._orchestrator_blocks_open,
        ensure_bound_strategy=svc._ensure_bound_strategy,
        load_strategy_by_id=svc._load_strategy_by_id,
        execute_paper_trade=svc._execute_paper_trade,
        try_execute_independent_agent_open=svc._try_execute_independent_agent_open,
        mark_master_decision_executed=svc._mark_master_decision_executed,
        backfill_dec_confidence_from_orch=svc._backfill_dec_confidence_from_orch,
        build_midlong_agent_envelope=svc._build_midlong_agent_envelope,
        midlong_persistence_allow=svc._midlong_persistence_allow,
        factor_veto_check=svc._factor_veto_check,
        get_today_realized_pnl=svc._get_today_realized_pnl,
        get_account_risk_score=svc._get_account_risk_score,
        tiny_close_allowed_by_hardfact=svc._tiny_close_allowed_by_hardfact,
        paper_loss_locks_disabled=svc._paper_loss_locks_disabled,
        safe_commit=svc._safe_commit,
        session_trading_mode=svc._session_trading_mode,
        extract_ai_position_pct=svc._extract_ai_position_pct,
        resolve_alignment_scale=svc._resolve_alignment_scale,
        resolve_decision_leverage=svc._resolve_decision_leverage,
        calibrate_confidence=svc._calibrate_confidence,
        ai_dynamic_position_pct=svc._ai_dynamic_position_pct,
        apply_tdi_position_advice=svc._apply_tdi_position_advice,
        get_direction_win_rate=svc._get_direction_win_rate,
        get_symbol_direction_wr=svc._get_symbol_direction_wr,
        log_pipeline_audit=svc._log_pipeline_audit,
        validate_tp_sl_by_nature=svc._validate_tp_sl_by_nature,
        is_reduce_cooldown_exempt=svc._is_reduce_cooldown_exempt,
        should_evaluate_position=svc._should_evaluate_position,
        record_position_decision=svc._record_position_decision,
        clear_deferred_signal=svc._clear_deferred_signal,
        deferred_signal_key=svc._deferred_signal_key,
        clear_hold_timeout_queue_entry=svc._clear_hold_timeout_queue_entry,
        event_scope_label=svc._event_scope_label,
        append_event=svc._append_event,
        # [2026-07-11 修复] 见上方 MasterExecutionHost 字段定义处注释。
        TIER_PROTECTION=svc.TIER_PROTECTION,
        DEFAULT_PROTECTION=getattr(
            svc, "DEFAULT_PROTECTION", {"protect_min": 30, "emergency_pct": -5.0}
        ),
        # [P3-修复] 透传 svc 状态给模块级函数使用
        last_unified_snapshot=getattr(svc, "_last_unified_snapshot", None),
        last_orch_decisions_ts=float(getattr(svc, "_last_orch_decisions_ts", 0) or 0),
        scalp_traded_this_tick=getattr(svc, "_scalp_traded_this_tick", set()),
        training_allowed_symbols=getattr(svc, "_training_allowed_symbols", set()),
    )
    if not hasattr(svc, "_master_strat_cache"):
        svc._master_strat_cache = host.master_strat_cache
    return host


def execute_master_decisions(
    db: Session,
    session,
    account_id: int,
    decisions: List[Dict],
    positions_list: List[Dict],
    active_ids: list,
    market_summary: dict,
    mode: str,
    host: MasterExecutionHost,
    analyst_reports: dict = None,
    balance_info: dict = None,
    orch_directions: dict = None,
    strat_tier_map: dict = None,
) -> None:
    from backend.services.paper_trading_engine import paper_engine
    from backend.database.models import AIStrategy as _AIStrategy, Account as _Account
    from backend.services.ai_decision_service import save_ai_decision
    # 优化：循环内 import 提升到方法顶层（减少 N 次 dict 查找）
    from backend.config.settings import STRICT_DATA_GATE, ORCHESTRATOR_WAIT_OVERRIDE_CONF, get_orchestrator_hard_gate
    from backend.services.data_readiness_gate import allow_open_action
    from backend.services.sub_position_manager import normalize_nature
    from backend.services.decision_core.direction_coherence import evaluate_direction_coherence
    from backend.services.decision_consistency_gate import get_consistency_gate
    from backend.services.swing_agent import swing_agent
    from backend.services.trend_agent import trend_agent
    from backend.services.scalp_factor_router import scalp_factor_router
    # 优化：DecisionSnapshot/AIDecisionLog imports 提升到顶层（减少循环内重复 import）
    from backend.database.models import DecisionSnapshot, AIDecisionLog

    # ── 预计算编排器 frozen/wait 状态（同一 tick 内 market_summary 不变）──
    _precomputed_orch_state: Dict[str, str] = {}  # sym.upper() → "frozen"|"wait"|"ok"
    _orchestrator_hard_gate_active = get_orchestrator_hard_gate(mode)
    if _orchestrator_hard_gate_active:
        for _psym, _pinfo in (market_summary or {}).items():
            if not isinstance(_pinfo, dict):
                continue
            _poch = _pinfo.get("orchestrator", {})
            if not isinstance(_poch, dict):
                continue
            _poch_action = (_poch.get("action") or _poch.get("final_action") or "").strip().lower()
            if _poch_action in ("frozen", "wait"):
                _precomputed_orch_state[_psym.upper()] = _poch_action
                _precomputed_orch_state[f"{_psym.upper()}_reason"] = str(_poch.get("reasoning", "") or _poch_action)[:120]

    # 防御性 rollback：分析阶段可能有查询失败导致 session 事务污染
    # [fix] rollback 后 merge session 避免 "not persistent" 错误
    try:
        db.rollback()
        session = db.merge(session)
    except Exception:
        pass
    host.clear_master_strat_cache()

    _account = db.query(_Account).filter(_Account.id == account_id).first()

    position_map = {}       # key: "BTC_swing" -> position dict（nature 优先索引）
    nature_map = {}         # key: "BTC_swing" -> position dict（保留向后兼容）
    symbol_positions = {}   # key: "BTC" -> [pos1, pos2, ...]（同 symbol 所有子仓位）
    for p in (positions_list or []):
        sym = p.get("symbol", "")
        # F2-fix: 主键改用 trade_nature（子仓位唯一标识），
        # 不再用 timeframe_tier 避免 trend_follow/position 都映射 long 导致碰撞
        _pn = p.get("trade_nature") or ""
        if _pn:
            nature_map[f"{sym}_{_pn}"] = p
            position_map[f"{sym}_{_pn}"] = p
        # tier 索引仅作 fallback（旧仓位无 trade_nature 时）
        tier = p.get("timeframe_tier") or "mid"
        pos_key = f"{sym}_{tier}"
        if pos_key not in position_map:
            position_map[pos_key] = p
        symbol_positions.setdefault(sym, []).append(p)

    # ── 硬风控门槛：RiskAnalyst score > 80 → 拦截所有开仓 ──
    # 加入衰减机制：连续 hold 不开仓时 risk_score 逐步衰减，避免永久锁定
    risk_block_new_positions = False
    risk_report = (analyst_reports or {}).get("risk")
    _lock_profile = host.get_lock_profile(session)
    _risk_block_threshold = _lock_profile.risk_score_block_threshold
    if risk_report and _risk_block_threshold is not None and not _lock_profile.disable_loss_locks:
        r = risk_report if isinstance(risk_report, dict) else (
            risk_report.to_dict() if hasattr(risk_report, 'to_dict') else {})
        risk_score = r.get("risk_score", 50)
        # 衰减机制：记录连续拦截次数，每次衰减 5 分
        _blk_key = f"risk_blk_{session.session_id}"
        _blk_count = host.partial_close_tracker.get(_blk_key, {}).get("count", 0)
        if risk_score > _risk_block_threshold:
            _blk_count += 1
            effective_score = risk_score - (_blk_count - 1) * 5
            host.partial_close_tracker[_blk_key] = {"count": _blk_count, "reset_at": time.time() + 600}
            if effective_score > _risk_block_threshold:
                risk_block_new_positions = True
                host.append_event(session, "risk_gate",
                    f"⛔ 风险分数 {risk_score:.0f}/100(衰减后{effective_score:.0f}) 过高，拦截所有新开仓(连续{_blk_count}次，阈值{_risk_block_threshold})")
                logger.warning(f"[FullAuto] 硬风控拦截: risk_score={risk_score:.0f}→{effective_score:.0f}>{_risk_block_threshold}, 禁止开新仓(连续{_blk_count}次)")
            else:
                host.append_event(session, "risk_gate",
                    f"✅ 风险分数 {risk_score:.0f}/100 衰减后{effective_score:.0f}≤{_risk_block_threshold}，允许开仓")
                logger.info(f"[FullAuto] 硬风控衰减放行: risk_score={risk_score:.0f}→{effective_score:.0f}≤{_risk_block_threshold}(连续{_blk_count}次)")
        else:
            # risk_score 正常，重置计数
            if _blk_count > 0:
                host.partial_close_tracker.pop(_blk_key, None)
                logger.info(f"[FullAuto] 硬风控重置: risk_score={risk_score:.0f}≤{_risk_block_threshold}")

    # ── 硬风控门槛：同向持仓敞口限制 ──
    total_equity = float((balance_info or {}).get("total_equity", 0))
    long_margin_total = sum(
        float(p.get("margin", 0)) for p in (positions_list or []) if p.get("side") == "long")
    short_margin_total = sum(
        float(p.get("margin", 0)) for p in (positions_list or []) if p.get("side") == "short")

    # ── 多周期并行：per-tier 保证金追踪 ──
    from backend.config.settings import TIER_BUDGET_ALLOCATION, TIER_MAX_MARGIN_PCT
    _tier_margin_used: Dict[str, float] = {"short": 0, "mid": 0, "long": 0}
    for p in (positions_list or []):
        _p_tier = p.get("timeframe_tier") or "mid"
        _p_nature = p.get("trade_nature", "")
        if _p_nature:
            from backend.services.sub_position_manager import NATURE_TO_TIER
            _p_tier = NATURE_TO_TIER.get(_p_nature, _p_tier)
        _tier_margin_used[_p_tier] = _tier_margin_used.get(_p_tier, 0) + float(p.get("margin", 0))

    _tier_budget_caps: Dict[str, float] = {}
    for _t in ("short", "mid", "long"):
        _alloc = TIER_BUDGET_ALLOCATION.get(_t, 0.3)
        _max_pct = TIER_MAX_MARGIN_PCT.get(_t, 0.4)
        _tier_budget_caps[_t] = total_equity * min(_alloc, _max_pct)

    _position_dirty = False  # 标记：本轮循环中是否发生了仓位变更

    # ── 检查延迟排队信号：将冷却期已满的排队信号重注入到决策列表──
    _now = time.time()
    _expired_defer_keys = [
        k for k, v in host.deferred_signals.items()
        if _now >= v["cooldown_expires"]
        and v.get("account_id") == account_id
    ]
    _injected_decisions = []
    for _dk in _expired_defer_keys:
        _ds = host.deferred_signals.pop(_dk)
        _defer_tier = (_ds.get("timeframe_tier") or "mid").strip().lower()
        _orch_blk, _orch_why = host.orchestrator_blocks_open(
            _ds["symbol"], _ds["action"], market_summary, _defer_tier,
            confidence=_ds.get("confidence", 0), trading_mode=mode,
        )
        if _orch_blk:
            logger.info(
                f"[FullAuto] 延迟信号丢弃 {_ds['symbol']} {_ds['action']}[{_defer_tier}]: "
                f"编排器仍拦截 ({_orch_why[:60]})"
            )
            continue
        _retry = int(_ds.get("defer_count", 0) or 0) + 1
        if _retry > host.deferred_max_retries:
            logger.info(
                f"[FullAuto] 延迟信号超限丢弃 {_ds['symbol']} {_ds['action']}: "
                f"已重试{_retry - 1}次"
            )
            continue
        _elapsed = _now - _ds["deferred_at"]
        logger.info(
            f"[FullAuto] 延迟信号重试: {_ds['symbol']} {_ds['action']}, "
            f"排队时间{_elapsed:.0f}s (第{_retry}次)"
        )
        _injected_decisions.append({
            "symbol": _ds["symbol"],
            "action": _ds["action"],
            "confidence": _ds.get("confidence", 50),
            "reasoning": f"[deferred+{_elapsed:.0f}s] " + _ds.get("reasoning", ""),
            "trade_nature": _ds.get("trade_nature", ""),
            "timeframe_tier": _defer_tier,
            "strategy_id": _ds.get("strategy_id"),
            "defer_count": _retry,
        })
    if _injected_decisions:
        decisions = list(decisions) + _injected_decisions
        logger.info(
            f"[FullAuto] 共重注延迟信号 {len(_injected_decisions)} 个: "
            + ", ".join(f"{d['symbol']} {d['action']}" for d in _injected_decisions)
        )

    # ── 多周期扇出：将单一决策展开为各tier独立决策 ──
    # 如果 orch_directions/strat_tier_map 未传入，从 host.market_scan_cache 和 active_ids 构建
    if orch_directions is None:
        _orch_dir = {}
        for sym, info in (market_summary or {}).items():
            if isinstance(info, dict):
                _oc = info.get("orchestrator", {})
                if isinstance(_oc, dict):
                    _orch_dir[sym.upper()] = {
                        "long_bias": _oc.get("long_bias", "neutral"),
                        "long_confidence": _oc.get("long_confidence", 0),
                        "mid_bias": _oc.get("mid_bias", "neutral"),
                        "mid_confidence": _oc.get("mid_confidence", 0),
                        "short_bias": _oc.get("short_bias", "neutral"),
                        "short_confidence": _oc.get("short_confidence", 0),
                        "final_action": _oc.get("final_action", _oc.get("action", "")),
                        "allowed_direction": _oc.get("allowed_direction", _oc.get("direction", "both")),
                    }
        # 也从 host.market_scan_cache 补充
        for sym, cache in (host.market_scan_cache or {}).items():
            sym_up = sym.upper()
            if sym_up not in _orch_dir:
                _oc = cache.get("orchestrator", {}) if isinstance(cache, dict) else {}
                if isinstance(_oc, dict) and _oc.get("long_bias"):
                    _orch_dir[sym_up] = {
                        "long_bias": _oc.get("long_bias", "neutral"),
                        "long_confidence": _oc.get("long_confidence", 0),
                        "mid_bias": _oc.get("mid_bias", "neutral"),
                        "mid_confidence": _oc.get("mid_confidence", 0),
                        "short_bias": _oc.get("short_bias", "neutral"),
                        "short_confidence": _oc.get("short_confidence", 0),
                        "final_action": _oc.get("final_action", _oc.get("action", "")),
                        "allowed_direction": _oc.get("allowed_direction", _oc.get("direction", "both")),
                    }
    else:
        _orch_dir = orch_directions

    if strat_tier_map is None:
        _stm = {}
        if active_ids:
            _strats_for_map = db.query(_AIStrategy).filter(
                _AIStrategy.strategy_id.in_(list(active_ids)),
                _AIStrategy.status.in_(["active", "paused"]),
            ).all()
            for _s in _strats_for_map:
                _s_tier = getattr(_s, 'timeframe_tier', None) or 'mid'
                _stm[(_s.primary_symbol.upper() if _s.primary_symbol else '', _s_tier)] = _s
    else:
        _stm = strat_tier_map

    if _stm and _orch_dir:
        decisions = host.expand_multi_tier_decisions(
            decisions, _stm, _orch_dir, session)

    # DecisionSnapshot / AIDecisionLog 属于 AnalyticsBase，需通过
    # AnalyticsSessionLocal 写入独立 analytics 数据库
    from backend.database.connection import AnalyticsSessionLocal
    _analytics_db = AnalyticsSessionLocal()

    # 2026-06-19: 跨 tier 反向开仓拦截（P0）
    # 追踪本 tick 内每个 symbol 已开仓的方向，防止 short tier 开多 + mid tier 开空
    _per_symbol_directions = {}  # symbol → {"buy": [tier1], "sell": [tier2]}

    # 2026-06-19: 全局敞口追踪（P0）
    # 追踪本 tick 内已用的保证金，防止三层同时满仓超过全局上限
    _tick_used_margin = 0.0
    try:
        _equity_for_cap = float((balance_info or {}).get("equity", 0) or (balance_info or {}).get("total_equity", 0) or 0)
    except Exception:
        _equity_for_cap = 0.0
    _GLOBAL_MAX_MARGIN_PCT = 0.90  # 三层总保证金不超过 90% 权益

    # 2026-06-19: P1 跨 tier 持仓可见性 — 注入 portfolio 到 market_summary
    # 让 SwingAgent/TrendAgent 的 LLM 能看到其他 tier 的持仓
    _portfolio_for_agents = {"positions": positions_list} if positions_list else None

    # 优化：批量收集 analytics 写入，循环结束后统一 commit
    _pending_snapshots: list = []  # DecisionSnapshot 对象列表
    _pending_logs: list = []       # AIDecisionLog 对象列表

    for dec in decisions:
        # 优化：只在上一轮交易确实发生了仓位变更时才 rollback
        try:
            if _position_dirty:
                db.rollback()
                long_margin_total, short_margin_total = host.refresh_positions_local(
                    db, account_id, positions_list, position_map, symbol_positions)
                _position_dirty = False
        except Exception:
            pass

        sym = dec.get("symbol", "")
        action = dec.get("action", "hold")
        raw_confidence = dec.get("confidence", 50)
        reasoning = dec.get("reasoning", "")

        # [2026-08-17 仲裁 Gate] 多源方向一致性校验（fail-closed）：
        # 与 scalp 独立循环等来源对同一 (symbol, tier) 的相反观点冲突时拒绝开仓。
        try:
            from backend.services.full_auto.decision_arbitration import check_entry, register_view
            _dec_tier_arb = (dec.get("tier") or dec.get("timeframe_tier") or "short").lower()
            register_view(sym, _dec_tier_arb, "master", action, raw_confidence)
            _arb_ok, _arb_why = check_entry(sym, _dec_tier_arb, "master", action, raw_confidence)
            if not _arb_ok and action in ("buy", "sell", "pyramid", "dca"):
                logger.info(
                    "[ArbGate] master 决策被仲裁拒绝 %s/%s %s -> hold (%s)",
                    sym, _dec_tier_arb, action, _arb_why,
                )
                action = "hold"
                reasoning = f"arb_conflict:{_arb_why}; {reasoning}"[:300]
        except Exception as _arb_err:
            logger.debug("[ArbGate] 仲裁跳过: %s", _arb_err)

        # [2026-07-30 根源修复] 只处理 session 固定配置的交易对。
        # market_summary 可能包含 scalp 交易的 KAITO/XMR/ZEC 等币，
        # 这些不在 session.symbols 里，不该进入长线 MLTO/趋势分析。
        # 之前只在 L760 的 trend_agent 段做了过滤，但 execute_mlto_lane
        # 在 L785 调用前虽然检查了 _sym_is_auto_coin，但 dec 本身是从
        # expand_multi_tier_decisions 来的，expand 的输入是 AI 分析全部 symbol。
        # 修复：在主循环入口直接过滤。
        try:
            from backend.services.auto_coin_selector import is_long_allowed
            _dec_tier = (dec.get("tier") or dec.get("timeframe_tier") or "").lower()
            if _dec_tier == "long" and not is_long_allowed(sym, getattr(session, "session_id", ""), db=db):
                continue
        except Exception:
            pass

        try:
            if STRICT_DATA_GATE and action in ("buy", "sell", "pyramid", "dca"):
                _mkt_gate = (market_summary or {}).get(sym, {}) if isinstance(market_summary, dict) else {}
                _ok, _why = allow_open_action(
                    sym, action,
                    # [P3-修复] 原 getattr(self,...) 在模块级函数中抛 NameError 被吞 →
                    # STRICT_DATA_GATE 数据就绪门从不生效（fail-open）。改读 host。
                    snapshot=getattr(host, "last_unified_snapshot", None),
                    market_info=_mkt_gate,
                    source="master_execute",
                )
                if not _ok:
                    action = "hold"
                    dec["action"] = "hold"
                    raw_confidence = min(int(raw_confidence or 0), 10)
                    reasoning = f"{_why} | {reasoning}"
        except Exception as _dg_err:
            logger.debug(f"[FullAuto] data gate: {_dg_err}")

        # ════════════════════════════════════════════════════════════
        # 三层独立架构（Hierarchical 模式 — arXiv 2501.00826）
        # trade_nature 从策略的 timeframe_tier 推导，不从 MasterController AI 输出读。
        # 这确保中线 SwingAgent 和长线 TrendAgent 每轮独立触发。
        # ════════════════════════════════════════════════════════════
        # 从策略 tier 推导 nature（不依赖 AI 的 trade_nature 输出）
        _strategy_tier = (dec.get("tier") or dec.get("_source_tier") or dec.get("_fan_tier") or "").lower()
        _tier_nature_map = {"short": "scalp", "mid": "swing", "long": "trend_follow"}
        if _strategy_tier in _tier_nature_map:
            _dec_nature_raw = _tier_nature_map[_strategy_tier]
        else:
            # 回退：从 AI 输出读（但不再让 AI 覆盖编排器/策略 tier 的判断）
            _dec_nature_raw = (dec.get("trade_nature") or "").lower()
            if not _dec_nature_raw:
                _dec_nature_raw = "swing"  # 默认中线

        # AI 选币只属于短线池：主控扇出常把无策略币标成 mid，导致活动面板
        # 「长线(含中周期)」混入 FARTCOIN/UNI。强制 scalp/short（含误标 mid/long）。
        try:
            from backend.services.auto_coin_selector import is_auto_coin_symbol

            _sid = getattr(session, "session_id", "") or ""
            if _sid and is_auto_coin_symbol(sym, session_id=_sid) and _strategy_tier != "short":
                _dec_nature_raw = "scalp"
                dec["tier"] = "short"
                dec["_source_tier"] = "short"
                _strategy_tier = "short"
        except Exception:
            pass
        # ── MidLongExecutionLane: Master 路径委托 mid/long 新开给独立循环 ──
        try:
            from backend.config.settings import MIDLONG_MASTER_DELEGATE
            if MIDLONG_MASTER_DELEGATE:
                from backend.services.swing_agent import swing_agent as _swing_router
                from backend.services.trend_agent import trend_agent as _trend_router
                # [中长线合并] 中线已并入长线（mid_view 统一提供）：所有非短线策略
                # 的新开均由中长线链路负责，总控不再单独跑中线/长线 LLM 决策。
                _is_ml_delegate = (str(_dec_nature_raw).lower() not in ("scalp", "intraday"))
                # [Phase 5] 仅中长线**新开(buy/sell)** 委托独立循环（Single Writer）；
                # 滚仓(pyramid)/补仓(dca)不再被跳过——放行到下方既有分支执行，
                # 受 trend_pyramid_gate 5层门控 + tier 分档 + 冷却 + add_count 上限保护，
                # 与模式 B 共用同一门控状态，不会双写。
                if _is_ml_delegate and action in ("buy", "sell"):
                    logger.debug(
                        "[FullAuto][MidLongLane] Master 跳过 %s %s nature=%s（由 MidLongAgent独立 负责）",
                        sym, action, _dec_nature_raw,
                    )
                    continue
        except Exception:
            pass

        # ── ScalpExecutionLane: Master 路径 hard block scalp/intraday 新开 ──
        try:
            from backend.config.settings import SCALP_MASTER_HARD_BLOCK
            if SCALP_MASTER_HARD_BLOCK and scalp_factor_router.is_scalp_nature(_dec_nature_raw):
                if action in ("buy", "sell", "pyramid", "dca"):
                    logger.debug(
                        "[FullAuto][ScalpLane] Master 跳过 %s %s nature=%s（由 ScalpExecutionLane 负责）",
                        sym, action, _dec_nature_raw,
                    )
                    continue
        except Exception:
            pass

        # ── 短线层：已被 _run_scalp_independent 处理，这里跳过 ──
        try:
            if scalp_factor_router.is_scalp_nature(_dec_nature_raw):
                if sym.upper() in getattr(host, "scalp_traded_this_tick", set()):
                    action = "hold"
                    dec["action"] = "hold"
                    reasoning = f"[ScalpRouter去重] {sym} 本tick已独立交易"
                # scalp 未被独立交易的也跳过（由独立路径处理），不在这里决策
        except Exception:
            pass

        # ── 修复 B：为 SwingAgent/TrendAgent 预加载多周期 K线+指标 ──
        # agent 的深度思考需要 1h/4h/1d 实际数据，原 market_summary 只有标量价格
        # 修复（2026-06-25）：所有策略都注入完整数据（不只 swing/trend），
        # 并补充 regime/orchestrator/fear_greed/衍生品 等缺失字段
        from backend.services.swing_agent import swing_agent
        from backend.services.trend_agent import trend_agent
        # [中长线合并] 非短线策略（swing/trend/mean_reversion/position 等）统一视为
        # 中长线，由 long thesis + mid_view 链路分析，主循环不再重复调 LLM。
        _is_midlong_nature = (str(_dec_nature_raw).lower() not in ("scalp", "intraday"))
        _orch_stub = bool(dec.get("_orch_scheduled"))
        from backend.config.settings import (
            FULLAUTO_AI_DOMINANT,
            MIDLONG_AI_MANDATORY,
            MIDLONG_AGENT_INDEPENDENT_SCHEDULER,
            MIDLONG_MASTER_DELEGATE,
            MASTER_MIDLONG_LLM_MODE,
        )
        _skip_agent_llm = (
            False
            if (FULLAUTO_AI_DOMINANT or (MIDLONG_AI_MANDATORY and _is_midlong_nature))
            else (_orch_stub and dec.get("_orch_slot_action") != "create")
        )
        # 独立 mid/long 循环已负责 LLM+开单时，主循环不再重复调 Swing/Trend
        if MIDLONG_MASTER_DELEGATE and MIDLONG_AGENT_INDEPENDENT_SCHEDULER and _is_midlong_nature:
            _skip_agent_llm = True
        # MidLong v2：summary=强制减负（不重跑深度 LLM）；full=允许旧行为
        if (
            _is_midlong_nature
            and MIDLONG_AGENT_INDEPENDENT_SCHEDULER
            and str(MASTER_MIDLONG_LLM_MODE or "summary").strip().lower() == "summary"
        ):
            _skip_agent_llm = True
        _need_agent_data = _is_midlong_nature or _orch_stub
        if _need_agent_data and sym in (market_summary or {}):
            _ms_sym = market_summary[sym]
            if not isinstance(_ms_sym, dict):
                _ms_sym = {}
                market_summary[sym] = _ms_sym
            if "indicators_1h" not in _ms_sym:
                try:
                    from backend.services.kline_data_service import kline_service as _ks
                    import pandas as _kp
                    _tf_pairs = [("15m", "15m"), ("1h", "1h"), ("4h", "4h"), ("1d", "1d")]
                    if trend_agent.is_trend_nature(_dec_nature_raw):
                        _tf_pairs.append(("1w", "1w"))
                    for _tf, _tf_key in _tf_pairs:
                        _min_bars = {"1w": 8, "1d": 20, "4h": 20, "1h": 20, "15m": 20}.get(_tf, 20)
                        # 决策热路径：仅 data_center(purpose=trade)，禁止 get_kline_data 旁路
                        _raw_kl = _ks.get_klines_from_db(sym, _tf, count=60)
                        if not _raw_kl or len(_raw_kl) < _min_bars:
                            logger.debug(
                                "[Agent指标预加载] %s/%s K线不足(%s<%s)，跳过",
                                sym, _tf, len(_raw_kl or []), _min_bars,
                            )
                            continue
                        if _raw_kl and len(_raw_kl) >= _min_bars:
                            _kdf = _kp.DataFrame(_raw_kl)
                            if "datetime" not in _kdf.columns and "timestamp" in _kdf.columns:
                                _kdf["datetime"] = _kp.to_datetime(
                                    _kdf["timestamp"], unit="s", utc=True
                                ).astype(str)
                            _ind = {}
                            # RSI(14)
                            _delta = _kdf["close"].diff()
                            _gain = _delta.where(_delta > 0, 0.0)
                            _loss = (-_delta).where(_delta < 0, 0.0)
                            _avg_g = _gain.ewm(alpha=1/14, adjust=False).mean()
                            _avg_l = _loss.ewm(alpha=1/14, adjust=False).mean()
                            _rs = _avg_g / _avg_l.replace(0, 1e-10)
                            _ind["rsi"] = round(float((100 - 100 / (1 + _rs)).iloc[-1]), 1)
                            # EMA 趋势（9/21/50）
                            _ema9 = _kdf["close"].ewm(span=9, adjust=False).mean().iloc[-1]
                            _ema21 = _kdf["close"].ewm(span=21, adjust=False).mean().iloc[-1]
                            _ema50 = _kdf["close"].ewm(span=50, adjust=False).mean().iloc[-1] if len(_kdf) >= 50 else _ema21
                            _ind["ema9"] = round(float(_ema9), 2)
                            _ind["ema21"] = round(float(_ema21), 2)
                            _ind["ema50"] = round(float(_ema50), 2)
                            _ind["ema_trend"] = "bullish" if _ema9 > _ema21 > _ema50 else "bearish" if _ema9 < _ema21 < _ema50 else "mixed"
                            # MACD histogram
                            _ema_f = _kdf["close"].ewm(span=12, adjust=False).mean()
                            _ema_s = _kdf["close"].ewm(span=26, adjust=False).mean()
                            _macd = _ema_f - _ema_s
                            _sig = _macd.ewm(span=9, adjust=False).mean()
                            _ind["macd_hist"] = round(float((_macd - _sig).iloc[-1]), 4)
                            # 成交量比
                            if len(_kdf) >= 20:
                                _vol_ma = _kdf["volume"].iloc[-20:].mean()
                                _ind["vol_ratio"] = round(float(_kdf["volume"].iloc[-1] / _vol_ma), 2) if _vol_ma > 0 else 1.0
                            # 最近30根K线摘要（含时间轴，让 AI 有完整走势可分析）
                            # 修复（2026-06-25）：原 tail(5) 无 datetime，AI 只有5根蜡烛无法判断趋势
                            # 现在给30根带时间的K线，充分利用 DeepSeek 128K 上下文
                            _recent = _kdf.tail(30)[["datetime","open","high","low","close","volume"]].round(4) if "datetime" in _kdf.columns else _kdf.tail(30)[["open","high","low","close","volume"]].round(4)
                            _ind["recent_klines"] = _recent.to_dict("records")
                            # Fix 13: 衍生品指标注入（OI/CVD/Taker/Funding/Depth/Imbalance）
                            # 中线看 1h/4h 的 OI/CVD 变化，长线看 4h/1d 持仓量趋势
                            try:
                                from backend.services.market_flow_indicators import get_indicator_value as _giv2
                                _oi_d = _giv2(None, sym, "OI_DELTA", _tf)
                                if _oi_d is not None:
                                    _ind["oi_delta"] = round(float(_oi_d), 4)
                                _cvd_v = _giv2(None, sym, "CVD", _tf)
                                if _cvd_v is not None:
                                    _ind["cvd"] = round(float(_cvd_v), 1)
                                _tk_v = _giv2(None, sym, "TAKER", _tf)
                                if _tk_v is not None:
                                    _ind["taker_ratio"] = round(float(_tk_v), 3)
                                _dp_v = _giv2(None, sym, "DEPTH", _tf)
                                if _dp_v is not None:
                                    _ind["depth"] = round(float(_dp_v), 2)
                                _im_v = _giv2(None, sym, "IMBALANCE", _tf)
                                if _im_v is not None:
                                    _ind["imbalance"] = round(float(_im_v), 3)
                            except Exception:
                                pass
                            # funding_rate 从 market_summary 透传
                            _fr = (_ms_sym if isinstance(_ms_sym, dict) else {}).get("funding_rate")
                            if _fr is not None:
                                _ind["funding_rate"] = round(float(_fr) * 100, 5)
                            _ms_sym[f"indicators_{_tf_key}"] = _ind
                except Exception as _ind_err:
                    logger.debug(f"[Agent指标预加载] {sym} 跳过: {_ind_err}")

            # ── 修复（2026-06-25）：补充缺失的市场环境数据 ──
            # 原代码 market_summary 只有 price/funding，15个字段全 null
            # 现从 orchestrator/intelligence/crypto_alpha/衍生品 补全
            try:
                # orchestrator 三周期评估
                if "orchestrator" not in _ms_sym:
                    from backend.services.multi_timeframe_orchestrator import MultiTimeframeOrchestrator as _MTO
                    _mto = _MTO()
                    _od = _mto.get_last_decision(sym.upper())
                    if _od:
                        _ms_sym["orchestrator"] = {
                            "final_action": _od.final_action,
                            "final_side": _od.final_side,
                            "long_bias": _od.long_view.bias,
                            "long_confidence": _od.long_view.confidence,
                            "mid_bias": _od.mid_view.bias,
                            "mid_confidence": _od.mid_view.confidence,
                            "short_bias": _od.short_view.bias,
                            "short_confidence": _od.short_view.confidence,
                            "regime": _od.regime,
                            "sentiment_zone": _od.sentiment_zone,
                        }
            except Exception:
                pass

            try:
                # 市场状态 + 波动率 + ATR
                if not _ms_sym.get("regime"):
                    from backend.services.market_regime import MarketRegimeClassifier
                    import pandas as _mr_df
                    _kl_reg = None
                    for _tf_r in ["1h", "4h"]:
                        _raw = None
                        try:
                            from backend.services.kline_data_service import kline_service as _ks2
                            _raw = _ks2.get_klines_from_db(sym, _tf_r, count=100)
                        except Exception:
                            pass
                        if _raw and len(_raw) >= 50:
                            _kl_reg = _mr_df.DataFrame(_raw)
                            break
                    if _kl_reg is not None and len(_kl_reg) >= 50:
                        _clf = MarketRegimeClassifier()
                        _cls = _clf.classify(_kl_reg)
                        _ms_sym["regime"] = _cls.regime.value if hasattr(_cls.regime, 'value') else str(_cls.regime)
                        _ms_sym["volatility"] = "high" if _cls.regime.value in ("high_volatility","crash") else "normal"
                        # ATR
                        if len(_kl_reg) >= 14:
                            _tr = (_kl_reg["high"] - _kl_reg["low"]).rolling(14).mean()
                            _atr = float(_tr.iloc[-1]) if not _tr.isna().iloc[-1] else 0
                            _close = float(_kl_reg["close"].iloc[-1])
                            _ms_sym["atr"] = round(_atr, 2)
                            _ms_sym["atr_pct"] = round(_atr / _close * 100, 2) if _close > 0 else 0
            except Exception:
                pass

            try:
                # 币圈衍生品 alpha（清算/CVD/OBI/funding-OI 背离）
                if not _ms_sym.get("derivatives_signal"):
                    from backend.services.crypto_alpha_signals import crypto_alpha
                    _cab = crypto_alpha.get_bundle(sym)
                    if _cab.liquidation_magnet.available:
                        _ms_sym["derivatives_signal"] = _cab.liquidation_magnet.direction
                        _ms_sym["liq_severity"] = _cab.liquidation_magnet.severity
                    if _cab.cvd_pressure.available:
                        _ms_sym["cvd_direction"] = _cab.cvd_pressure.direction
                        _ms_sym["cvd_value"] = _cab.cvd_pressure.raw.get("ratio", 0)
                    if _cab.funding_oi_divergence.available:
                        _ms_sym["funding_oi_div"] = _cab.funding_oi_divergence.direction
                    if _cab.orderbook_imbalance.available:
                        _ms_sym["obi_direction"] = _cab.orderbook_imbalance.direction
            except Exception:
                pass

            try:
                # 情绪指数
                if not _ms_sym.get("fear_greed"):
                    from backend.services.intelligence_signal_engine import intelligence_signal_engine
                    _intel = intelligence_signal_engine.compute_trading_signal(sym)
                    if _intel:
                        _ms_sym["fear_greed"] = _intel.fear_greed_index
                        _ms_sym["whale_direction"] = _intel.whale_direction
                        _ms_sym["trend_direction"] = _intel.direction
            except Exception:
                pass

            # Fix 16b: agent 补充链上/宏观/期权数据（深度思考需要全维度市场数据）
            if "onchain_macro" not in _ms_sym:
                _ms_sym["onchain_macro"] = {}
                try:
                    from services.onchain_data_collector import onchain_collector as _oc2
                    _oc2_data = _oc2.collect_all([sym]).get(sym, {})
                    if isinstance(_oc2_data, dict):
                        for _k in ('fear_greed','active_addresses','exchange_net_flow',
                                   'tvl','btc_dominance','whale_tx_count','whale_tx_volume'):
                            _v = _oc2_data.get(_k)
                            if _v is not None and _v != 0:
                                _ms_sym["onchain_macro"][_k] = float(_v)
                except Exception:
                    pass
                try:
                    from backend.services.options_data_collector import get_options_for_symbol as _gof2
                    _opt2 = _gof2(sym)
                    if _opt2:
                        _ms_sym["onchain_macro"]["options"] = _opt2
                except Exception:
                    pass

            # Fix 21: 教训回流到 agent（SwingAgent/TrendAgent 的深度思考需要历史教训）
            # Fix 5 只注入了 Master LLM 的 prompt，agent 看不到 → 学习闭环对中长线断裂
            if "strategy_lessons" not in _ms_sym:
                try:
                    from backend.database.models import StrategyMemory
                    _mems = db.query(StrategyMemory).filter(
                        StrategyMemory.total_trades >= 3
                    ).order_by(StrategyMemory.updated_at.desc().nullslast()).limit(15).all()
                    _lessons_lines = []
                    _sym_upper = sym.upper()
                    for _m in _mems:
                        _kl = _m.key_lessons if isinstance(_m.key_lessons, list) else []
                        for _l in _kl[-2:]:
                            if isinstance(_l, dict):
                                _l_sym = str(_l.get("symbol", "")).upper()
                                _l_text = str(_l.get("lesson", ""))[:80]
                                if _l_text and (_l_sym == _sym_upper or _l_sym in ("", "*")):
                                    _lessons_lines.append(f"[{_l.get('type','')}] {_l_sym or sym}: {_l_text}")
                    if _lessons_lines:
                        _ms_sym["strategy_lessons"] = "\\n".join(_lessons_lines[:6])
                except Exception:
                    pass

        # ── [阶段4] 中线层（SwingAgent 独立分析）已废弃 ──
        # 中线分析能力完全由长线 thesis 的 mid_view 子结构提供（Phase 2 起 MLTO 的
        # qual_layer prompt 同时产出 long 方向 + mid_view；decision_hub 已含 mid_timing 权重）。
        # 原 SwingAgent.analyze 独立分支曾是"3 killers"路径之一，现随 mid-into-long 合并删除。
        # 注意：mid-tier 的 *路由检测* 仍依赖 swing_agent.is_swing_nature（见上方
        # MidLongExecutionLane delegate 与 agent 数据预加载段），SwingAgent 模块本身不删。
        # 中线性质决策在主循环这里直接保持 hold/既定 action，由独立 midlong_loop + MLTO
        # 长线 thesis（含 mid_view）统一处理。

        # ── 趋势层：默认 TrendAgent LLM；仅 MIDLONG_MLTO_CONTROLS_EXEC 时由 MLTO 控开单 ──
        # 2026-07-20：AI 选币币种不做长线分析（TrendAgent/MLTO 长线段都跳过）。
        # 只有会话固定配置的交易对才走长线，避免 AI 选的币被长线套住。
        # [阶段0 修复] 改用正向白名单 fresh DB 查询，避免 ORM 快照在长 tick 内漏入
        # 新注入的 auto-coin（getattr(session,"auto_coin_symbols") 是 stale 快照）。
        from backend.services.auto_coin_selector import get_fixed_symbols_for_session
        _fixed_symbols = get_fixed_symbols_for_session(session.session_id, db, tier="long")
        # 不在固定白名单 = auto-coin 或非会话币 → 都不应进长线
        _sym_is_auto_coin = sym.upper() not in _fixed_symbols
        try:
            if _sym_is_auto_coin:
                logger.debug("[TrendAgent] %s 为 AI 选币币种，跳过长线分析", sym)
            elif trend_agent.is_trend_nature(_dec_nature_raw):
                from backend.config.settings import (
                    MIDLONG_THESIS_LEDGER_ENABLED,
                    MIDLONG_MLTO_CONTROLS_EXEC,
                    MIDLONG_AI_MANDATORY,
                )
                _ms_sym = (market_summary or {}).get(sym) or {}
                if MIDLONG_THESIS_LEDGER_ENABLED and MIDLONG_MLTO_CONTROLS_EXEC:
                    # MidLong v2 Phase3：summary 模式不在 Master 路径重跑 MLTO 深度 LLM
                    if _skip_agent_llm or str(MASTER_MIDLONG_LLM_MODE or "summary").lower() == "summary":
                        action = "hold"
                        reasoning = (
                            f"[MidLong summary] Master 跳过 MLTO 深度路径，"
                            f"由独立循环负责 {sym} long"
                        )
                        raw_confidence = int(dec.get("confidence") or 0)
                        logger.debug(
                            "[FullAuto][MidLongLane] summary skip execute_mlto_lane %s",
                            sym,
                        )
                    else:
                        # [2026-08-17] 删除旧长线 LLM 分支（execute_mlto_lane + trend_agent.analyze_direction）。
                        # 长线唯一决策源是独立 midlong 循环的 long_trend_v2（规则化 L1 + Chandelier），
                        # Master 路径不再跑任何长线 LLM。
                        action = "hold"
                        reasoning = f"[MidLong v2] Master 长线 LLM 已下线，由独立循环 long_trend_v2 负责 {sym}"
                        raw_confidence = int(dec.get("confidence") or 0)
        except Exception as _trend_err:
            logger.debug(f"[TrendAgent] 趋势决策跳过: {_trend_err}")

        if trend_agent.is_trend_nature(_dec_nature_raw) and int(raw_confidence or 0) <= 0:
            raw_confidence = host.backfill_dec_confidence_from_orch(
                dec, sym=sym, market_summary=market_summary, tier="long",
            )

        # ── 中线/长线 2-tick 同向持久化门控（Fast Lane 路径在 evaluate_midlong_open 内处理，此处跳过）──
        if (
            action in ("buy", "sell")
            and dec.get("_agent_independent")
            and not dec.get("_mlto_handled")
            and not dec.get("_fast_lane_handled")
        ):
            _persist_nature = _dec_nature_raw or dec.get("trade_nature") or ""
            if not host.midlong_persistence_allow(sym, _persist_nature, action):
                try:
                    from backend.config.settings import MIDLONG_PERSISTENCE_TICKS
                    _pt = max(1, int(MIDLONG_PERSISTENCE_TICKS or 1))
                except Exception:
                    _pt = 2
                action = "hold"
                dec["action"] = "hold"
                reasoning = f"[Persistence] 需连续{_pt}tick同向才开仓 | {reasoning}"
                logger.info(f"[Persistence] {sym} {_persist_nature} 拦截(未达{_pt}tick)")

        # ════════════════════════════════════════════════════════════
        # 跨 tier 协调 + 全局敞口上限（2026-06-19/20 P0）
        # 注意：不同周期对同一 symbol 有不同方向是合理的对冲策略，不拦截！
        # 只拦截"同一 tick 内瞬间双开反向新仓"（纯浪费手续费）。
        # 例如：本轮 tick 里 short tier 先开了 BTC 空仓，同 tick 内 long tier 又开 BTC 多仓 → 拦截后者。
        # 但跨 tick 的持仓（上一轮开的 short 空仓 + 本轮要开 long 多仓）→ 允许。
        if action in ("buy", "sell"):
            _sym_up = sym.upper()
            _existing = _per_symbol_directions.get(_sym_up, {})

            # P0-1: 同 tick 内同 symbol 瞬间反向双开拦截
            _opposite = "sell" if action == "buy" else "buy"
            if _opposite in _existing:
                _opp_tiers = _existing[_opposite]
                logger.info(
                    f"[CrossTier] 拦截同tick瞬间反向 {sym} {action} "
                    f"(本tick已有 {_opposite}: tier={_opp_tiers}) — 防双开浪费"
                )
                action = "hold"
                dec["action"] = "hold"
                reasoning = f"[同tick反向双开拦截: {_opp_tiers}已开{_opposite}] {reasoning}"

            # P0-2: 全局敞口上限检查
            if action in ("buy", "sell") and _equity_for_cap > 0:
                _margin_est = float(dec.get("margin_usd", 0) or dec.get("_sizing_margin_usd", 0) or _equity_for_cap * 0.05)
                _total_after = _tick_used_margin + _margin_est
                _total_pct = _total_after / _equity_for_cap
                if _total_pct > _GLOBAL_MAX_MARGIN_PCT:
                    logger.info(
                        f"[CrossTier] 全局敞口拦截 {sym} {action} "
                        f"(本tick已用{_tick_used_margin:.0f}+{_margin_est:.0f}={_total_after:.0f} "
                        f"= {_total_pct:.0%}权益 > {_GLOBAL_MAX_MARGIN_PCT:.0%}上限)"
                    )
                    action = "hold"
                    dec["action"] = "hold"
                    reasoning = f"[全局敞口拦截: 总保证金{_total_pct:.0%}>上限] {reasoning}"

            # 记录本 tick 的开仓方向（用于后续决策的反向检测）
            if action in ("buy", "sell"):
                _per_symbol_directions.setdefault(_sym_up, {}).setdefault(action, []).append(
                    dec.get("tier") or dec.get("_fan_tier") or _dec_nature_raw or "?"
                )
                _tick_used_margin += float(dec.get("margin_usd", 0) or _equity_for_cap * 0.05)

        # F6-fix: AI nature 与编排器仲裁 — 编排器为权威来源
        # [tier-fix v13] 「带 tier 信息的决策」一律跳过 symbol 级 recommended_nature 覆盖：
        #   - 扇出决策(_fan_out=True)
        #   - TierParallelExecutor 生成的决策(_source_tier 非空)
        #   - 显式带 tier 字段的决策(dec.tier 明确)
        # 这些决策的 tier/nature 是 tier 专属、权威的，若被 symbol 级 recommended_nature
        # 覆盖会导致所有 tier 都塌缩到同一个 nature/tier（历史 bug：全部变 long）。
        _is_fan_out = bool(dec.get("_fan_out"))
        _source_tier = (dec.get("_source_tier") or "").strip().lower()
        _dec_tier = (dec.get("tier") or "").strip().lower()
        _fan_tier = (dec.get("_fan_tier") or "").strip().lower()

        # 三层独立架构（Hierarchical）：trade_nature 从策略 tier 推导，不从 AI 输出读。
        # L7562 已经根据策略 tier 设置了 _dec_nature_raw，这里直接用。
        trade_nature = normalize_nature(_dec_nature_raw) if _dec_nature_raw else "swing"

        # [tier-fix v13] tier 优先来源：_source_tier > _fan_tier > dec.tier > nature 反推
        _explicit_tier = _source_tier or _fan_tier or _dec_tier
        if _explicit_tier in ("short", "mid", "long"):
            tier = _explicit_tier
        else:
            tier = host.nature_to_tier_map.get(trade_nature, "mid")
        host.current_decision_tier = tier  # 用于跨 tier 保护
        scope_lbl = host.event_scope_label(trade_nature, tier)

        # ═══ Agent 独立 Fast Lane（设计 Phase1）：Swing/Trend → 组合门控 → 下单，跳过 18 层串行门控 ═══
        if (
            action in ("buy", "sell")
            and dec.get("_agent_independent")
            and trade_nature in ("swing", "trend_follow", "position")
        ):
            _fast_conf = int(raw_confidence or dec.get("confidence") or 0)
            _fast_ok = host.try_execute_independent_agent_open(
                db=db,
                session=session,
                sym=sym,
                tier=tier,
                action=action,
                confidence=_fast_conf,
                sl_pct=float(dec.get("stop_loss_pct") or 0.035),
                tp_pct=float(dec.get("take_profit_pct") or 0.07),
                trade_nature=trade_nature,
                market_summary=market_summary,
                session_mode=mode,
            )
            _fast_msg = (
                f"✅ {sym}[{scope_lbl}] Agent独立开单 {action} conf={_fast_conf}"
                if _fast_ok
                else f"⏸ {sym}[{scope_lbl}] Agent独立门控未放行 conf={_fast_conf}"
            )
            host.append_event(session, "master_decision", f"🎯 {_fast_msg} | {reasoning[:120]}")
            logger.info("[AgentFastLane] %s tier=%s %s ok=%s", sym, tier, action, _fast_ok)
            if _fast_ok:
                session.total_trades = (session.total_trades or 0) + 1
                try:
                    host.safe_commit(db, "agent_fast_lane_open", session=session)
                except Exception:
                    pass
            continue

        # ── 置信度校准：LLM 自报值与规则信号融合 ──
        confidence = host.calibrate_confidence(
            raw_confidence, action, sym, analyst_reports, market_summary)
        if confidence != raw_confidence:
            reasoning += f" [校准{raw_confidence}→{confidence}]"

        # ── P0 方向门控（DCP 单一权威）──
        if action in ("buy", "sell") and confidence > 0:
            try:
                _ms_sym = (market_summary or {}).get(sym, {}) if isinstance(market_summary, dict) else {}
                _oc = _ms_sym.get("orchestrator", {}) if isinstance(_ms_sym, dict) else {}
                _dcp = evaluate_direction_coherence(
                    action=action,
                    confidence=confidence,
                    tier=tier,
                    trade_nature=trade_nature,
                    orchestrator=_oc if isinstance(_oc, dict) else {},
                    fan_branch=dec.get("_fan_branch") or "",
                    symbol=sym,
                    trading_mode=mode,
                )
                if not _dcp.allowed:
                    _old_action = action
                    action = "hold"
                    confidence = 0
                    dec["action"] = "hold"
                    dec["confidence"] = 0
                    dec["_dir_gate_blocked"] = True
                    reasoning += f" [DCP拦截] {_dcp.rule}: {_dcp.reason}"
                    dec["reasoning"] = reasoning
                    logger.warning(
                        f"[FullAuto] DCP拦截 {sym}: {_old_action} → hold ({_dcp.rule})"
                    )
                elif _dcp.penalty > 0:
                    dec["_dcp_penalty"] = _dcp.penalty
                    reasoning += f" [DCP逆势例外+{_dcp.penalty}%门槛]"
                    dec["reasoning"] = reasoning
            except Exception as _dir_err:
                logger.warning(f"[FullAuto] 方向门控异常(拦截): {_dir_err}")
                if action in ("buy", "sell"):
                    host.append_event(session, "direction_gate_error",
                        f"方向门控异常，拦截 {sym} {action}: {_dir_err}")
                    continue

        # ── 编排器 wait/frozen 预拦截：Agent 独立路径走 soft 缩仓，不 hard block ──
        if (
            action in ("buy", "sell")
            and confidence > 0
            and not dec.get("_agent_independent")
        ):
            _orch_state = _precomputed_orch_state.get(sym.upper(), "")
            if _orch_state == "frozen":
                _orch_blk, _orch_why = True, _precomputed_orch_state.get(f"{sym.upper()}_reason", "frozen")
            elif _orch_state == "wait":
                _conf_val = float(confidence or 0)
                if _conf_val >= float(ORCHESTRATOR_WAIT_OVERRIDE_CONF):
                    _orch_blk, _orch_why = False, ""
                else:
                    _orch_blk, _orch_why = True, _precomputed_orch_state.get(f"{sym.upper()}_reason", "wait")
            else:
                _orch_blk, _orch_why = False, ""
            if _orch_blk:
                _old_action = action
                action = "hold"
                confidence = min(int(confidence or 0), 15)
                dec["action"] = "hold"
                dec["confidence"] = confidence
                dec["_orch_wait_blocked"] = True
                reasoning = (
                    f"编排器建议wait，取消{_old_action} | {_orch_why[:80]} | {reasoning}"
                )
                dec["reasoning"] = reasoning
                host.clear_deferred_signal(account_id, sym, _old_action, tier)
                logger.info(
                    f"[FullAuto] 编排器wait预拦截 {sym}[{tier}]: 取消{_old_action}"
                )

        # ── D2: 决策一致性门控（Agent 独立路径跳过）──
        _consistency_blocked = False
        if action in ("buy", "sell") and confidence > 0 and not dec.get("_agent_independent"):
            try:
                _gate = get_consistency_gate()
                _market_regime = None
                try:
                    _ms_sym = (market_summary or {}).get(sym, {}) if isinstance(market_summary, dict) else {}
                    _market_regime = _ms_sym.get("market_cycle") or _ms_sym.get("regime")
                except Exception:
                    pass
                _consistency_check = _gate.check(
                    account_id=account_id, symbol=sym,
                    action=action,
                    confidence=confidence / 100.0,
                    market_regime=_market_regime,
                )
                if not _consistency_check.passed:
                    action = "hold"
                    confidence = 0
                    _consistency_blocked = True
                    dec["action"] = "hold"
                    dec["confidence"] = 0
                    dec["_consistency_blocked"] = True
                    reasoning += f" [一致性门控拦截] {_consistency_check.reason}"
                    dec["reasoning"] = reasoning
                    logger.info(
                        f"[FullAuto] 一致性门控拦截 {sym} {action}: "
                        f"{_consistency_check.reason}"
                    )
            except Exception as _gate_err:
                logger.warning(f"[FullAuto] 一致性门控异常(拦截): {_gate_err}")
                if action in ("buy", "sell"):
                    dec["action"] = "hold"
                    dec["confidence"] = 0
                    dec["reasoning"] = (dec.get("reasoning") or reasoning) + f" [一致性门控异常拦截: {_gate_err}]"

        # 中长线"调度桩"抑制：当主循环把 mid/long 的 LLM 决策委派给独立循环
        # （_skip_agent_llm=True）时，dec 仍是未分析的占位桩——reasoning 是
        # "[中长线AI强制→SwingAgent LLM]" 占位符、confidence 是编排器多周期偏向
        # （非决策置信）。若记进决策日志，会与独立循环产出的真实 Swing/Trend 决策
        # 语义冲突，表现为"70% hold 无理由"+同条重复两次，误导运营。
        # 故未经 agent 分析的委派桩不再发 master_decision 事件；真实决策由独立循环记录。
        _unanalyzed_stub = bool(_orch_stub and _skip_agent_llm)
        if _unanalyzed_stub:
            logger.debug(
                "[FullAuto] 跳过中长线调度桩决策日志 %s[%s]（已委派独立循环，真实决策由独立循环记录）",
                sym, scope_lbl,
            )
        else:
            # 决策来源标注：一眼区分【硬约束拦截】与【模型主观观望】。
            # 硬拦截 = 代码层 gate 强制把 buy/sell 改成 hold（真实约束）；
            # 模型观望 = LLM 自己返回 hold（主观判断，其 reasoning 里的"额度/预算"等
            #            说辞不代表真有硬约束，仅供参考）。
            _src_tag = ""
            if action == "hold":
                if dec.get("_dir_gate_blocked"):
                    _src_tag = "【硬拦截·方向门控】"
                elif dec.get("_orch_wait_blocked"):
                    _src_tag = "【硬拦截·编排器wait】"
                elif dec.get("_consistency_blocked"):
                    _src_tag = "【硬拦截·一致性门控】"
                else:
                    _src_tag = "【模型观望】"
            host.append_event(session, "master_decision",
                f"🎯 {sym}[{scope_lbl}]: {action}{_src_tag} (置信={confidence}%) | {reasoning}")
            logger.info(
                f"[FullAuto] 总控决策 {sym}[{scope_lbl}]: {action}{_src_tag} conf={confidence}%")
        # 增量落库：避免整轮 AI 循环(8币×LLM)结束前 UI 决策日志长时间不更新
        try:
            host.safe_commit(db, "master_decision_event", session=session)
        except Exception as _evt_commit_err:
            logger.debug(f"[FullAuto] master_decision 增量落库跳过: {_evt_commit_err}")

        # ── 写入决策快照（自反思经验库，批量收集后统一写入）──
        try:
            mkt_snap = (market_summary or {}).get(sym, {}) if isinstance(market_summary, dict) else {}
            # 从 strat_map 解析 strategy_id（而非 AI JSON 响应，后者通常不含 strategy_id）
            _resolved_sid = ""
            try:
                _sm = locals().get('strat_map') or {}
                _s = _sm.get(sym)
                if _s and hasattr(_s, 'strategy_id'):
                    _resolved_sid = _s.strategy_id or ""
            except Exception:
                pass
            from backend.services.decision_core.proposal import TradeProposal
            from backend.services.decision_snapshot_writer import decision_snapshot_writer
            _prop = TradeProposal.from_agent(
                sym=sym,
                tier=tier or "mid",
                action=action,
                confidence=float(confidence or 0),
                trade_nature=(dec.get("trade_nature") or "swing"),
                source_lane="master",
                reasoning=(reasoning or "")[:500],
            )
            _code_reason = (
                dec.get("_gate_reason")
                or dec.get("hold_reason")
                or (dec.get("_agent_envelope") or {}).get("block_reason")
                or ""
            ).strip()
            _verdict = {
                "allowed": action in ("buy", "sell"),
                "reason": _code_reason,
                "code_reason": _code_reason,
                "layer": "master",
                "rule": dec.get("_gate_rule") or "master",
            }
            snap = decision_snapshot_writer.build(
                session_id=session.id if hasattr(session, "id") else None,
                strategy_id=_resolved_sid,
                symbol=sym,
                tier=tier,
                action=action,
                confidence=float(confidence or 0),
                reasoning=reasoning[:2000] if reasoning else None,
                market_snapshot={
                    "regime": mkt_snap.get("market_cycle") if isinstance(mkt_snap, dict) else None,
                    "volatility": mkt_snap.get("volatility_value") if isinstance(mkt_snap, dict) else None,
                    "trend": mkt_snap.get("trend_direction") if isinstance(mkt_snap, dict) else None,
                    "fear_greed": mkt_snap.get("sentiment_index") if isinstance(mkt_snap, dict) else None,
                    **({
                        "mlto": {
                            "thesis_id": (_mlto_env := (dec.get("_agent_envelope") or {})).get("thesis_id")
                                or (dec.get("_mlto_thesis") or {}).get("thesis_id"),
                            "open_readiness": _mlto_env.get("open_readiness")
                                or (dec.get("_mlto_thesis") or {}).get("open_readiness"),
                            "hub_adjusted": _mlto_env.get("hub_adjusted")
                                or (dec.get("_mlto_thesis") or {}).get("hub_adjusted"),
                        }
                    } if dec.get("_mlto_handled") or dec.get("_agent_envelope") else {}),
                },
                proposal=_prop.to_dict(),
                evaluate_verdict=_verdict,
                source_lane="master",
                proposal_id=_prop.proposal_id,
                trace_id=_prop.trace_id,
                execution_channel=host.session_trading_mode(session),
                account_id=int(getattr(session, "account_id", None) or 0),
                mode=host.session_trading_mode(session),
            )
            _snap_entry = snap
            _pending_snapshots.append(snap)
        except Exception as _snap_err:
            logger.warning(f"[FullAuto] 决策快照写入失败: {_snap_err}", exc_info=True)

        # ── 写入 AIDecisionLog（批量收集，统一提交）──
        # 注意：executed 初始均写 "false"；实际下单成功后由执行段更新为 "true"
        _dec_log_entry = None   # 用于后续执行成功时回写 executed=true
        _snap_entry = None      # 对应 DecisionSnapshot，与 _dec_log_entry 同步回写
        try:
            # 暴露每个分支跳过的原因，定位为什么没写日志
            if not _account:
                logger.warning(f"[FullAuto] AIDecisionLog 跳过 {sym}: _account 为 None")
            elif not sym:
                logger.warning(f"[FullAuto] AIDecisionLog 跳过: symbol 为空")
            elif action not in ("buy", "sell", "hold", "close", "reduce"):
                logger.warning(f"[FullAuto] AIDecisionLog 跳过 {sym}: action='{action}' 不在白名单")

            if _account and sym and action in ("buy", "sell", "hold", "close", "reduce"):
                from decimal import Decimal as _Decimal
                _total_assets = float((balance_info or {}).get("total_equity", 10000) or 10000)
                _target_portion = float(dec.get("position_pct", 0) or 0)
                _prev_portion = 0.0
                for _pp in (positions_list or []):
                    if (_pp.get("symbol") or "").upper() == sym.upper():
                        _val = float(_pp.get("value", 0) or _pp.get("notional", 0) or 0)
                        if _total_assets > 0:
                            _prev_portion = _val / _total_assets
                        break

                # ── 已有同向同 nature 仓位时，buy/sell 降级为 hold 日志（防止噪音污染）──
                # 不同 nature（如 swing/intraday）允许并存，不降级
                _log_action = action
                _log_reason = reasoning[:500] if reasoning else f"[总控] {action} {sym}"
                if action in ("buy", "sell"):
                    _want_side = "long" if action == "buy" else "short"
                    _has_same_dir = any(
                        (p.get("symbol") or "").upper() == sym.upper()
                        and p.get("side") == _want_side
                        and (p.get("trade_nature") or "swing") == (trade_nature or "swing")
                        for p in (positions_list or [])
                    )
                    if _has_same_dir:
                        _log_action = "hold"
                        _log_reason = (
                            f"[已有{_want_side}仓位] {sym}[{trade_nature or 'swing'}] 跳过重复{action}信号，维持持仓 | "
                            + _log_reason[:200]
                        )

                _reasoning_snap = None
                if reasoning:
                    _reasoning_snap = reasoning[:4000]
                elif _log_reason:
                    _reasoning_snap = _log_reason[:4000]
                # ── [2026-06-21] 从 orchestrator 提取三周期 bias ──
                _mkt_orch = ((market_summary or {}).get(sym, {}) or {}).get("orchestrator", {})
                _mkt_orch = _mkt_orch if isinstance(_mkt_orch, dict) else {}
                _dec_log_entry = AIDecisionLog(
                    account_id=_account.id,
                    reason=_log_reason,
                    operation=_log_action,
                    symbol=sym.upper(),
                    prev_portion=_Decimal(str(round(_prev_portion, 6))),
                    target_portion=_Decimal(str(round(_target_portion, 6))),
                    total_balance=_Decimal(str(round(_total_assets, 2))),
                    executed="false",
                    reasoning_snapshot=_reasoning_snap,
                    ai_strategy_id=dec.get("strategy_id"),
                    # [v6 S2-6] 注入 hub 模式/灰度权重 → 决策日志灰度对比（ai_governed_compare）
                    decision_snapshot=json.dumps(
                        _hub_mode_snapshot({
                            "trade_nature": dec.get("trade_nature") or "",
                            "tier": tier,
                            "confidence": confidence,
                            "reasoning": (reasoning or "")[:2000],
                            "agent_source": (dec.get("_agent_envelope") or {}).get("agent_source")
                            or dec.get("_decision_source")
                            or "",
                            "alignment_score": (dec.get("_agent_envelope") or {}).get("alignment_score"),
                            "cited_fact_ids": (dec.get("_agent_envelope") or {}).get("cited_fact_ids"),
                            **({"agent_envelope": dec.get("_agent_envelope")} if dec.get("_agent_envelope") else {}),
                            **({"agent_evidence": dec.get("_agent_evidence_audit")} if dec.get("_agent_evidence_audit") else {}),
                        }),
                        ensure_ascii=False,
                    ),
                    decision_source=dec.get("_decision_source") or (
                        (dec.get("_agent_envelope") or {}).get("agent_source") or "llm"
                    ),
                    # [2026-06-21] 三周期 bias 注入（来自 MultiTimeframeOrchestrator）
                    short_bias=str(_mkt_orch.get("short_bias") or _mkt_orch.get("s_bias") or "") or None if isinstance(_mkt_orch, dict) else None,
                    short_confidence=float(_mkt_orch.get("short_confidence") or _mkt_orch.get("s_confidence") or 0) if isinstance(_mkt_orch, dict) else None,
                    mid_bias=str(_mkt_orch.get("mid_bias") or _mkt_orch.get("m_bias") or "") or None if isinstance(_mkt_orch, dict) else None,
                    mid_confidence=float(_mkt_orch.get("mid_confidence") or _mkt_orch.get("m_confidence") or 0) if isinstance(_mkt_orch, dict) else None,
                    long_bias=str(_mkt_orch.get("long_bias") or _mkt_orch.get("l_bias") or "") or None if isinstance(_mkt_orch, dict) else None,
                    long_confidence=float(_mkt_orch.get("long_confidence") or _mkt_orch.get("l_confidence") or 0) if isinstance(_mkt_orch, dict) else None,
                )
                _pending_logs.append(_dec_log_entry)
                try:
                    _analytics_db.add(_dec_log_entry)
                    _analytics_db.commit()
                except Exception as _inline_log_err:
                    try:
                        _analytics_db.rollback()
                    except Exception:
                        pass
                    logger.warning(
                        f"[FullAuto] AIDecisionLog 增量提交失败 sym={sym}: {_inline_log_err}"
                    )
        except Exception as _dec_log_err:
            # Bug D 修复：失败必须可观察，不能被 logger.debug 吞掉
            logger.warning(
                f"[FullAuto] AIDecisionLog 写入失败 sym={sym} action={action} "
                f"acct={getattr(_account, 'id', None)}: {_dec_log_err}",
                exc_info=True,
            )

        pos_key = f"{sym}_{tier}"
        # 优先通过 trade_nature 查找目标子仓位
        pos = nature_map.get(f"{sym}_{trade_nature}")
        if pos is None:
            pos = position_map.get(pos_key)
        # MidLong v2：中长线一体 — swing/trend_follow/position 可互为别名匹配仓位
        _MIDLONG_NATURE_ALIAS = frozenset({"swing", "trend_follow", "position"})
        _dec_n = (trade_nature or "").strip().lower()
        if pos is None and action in ("close", "reduce") and _dec_n in _MIDLONG_NATURE_ALIAS:
            for _cand in symbol_positions.get(sym, []) or []:
                _cn = (_cand.get("trade_nature") or "").strip().lower()
                if _cn in _MIDLONG_NATURE_ALIAS:
                    pos = _cand
                    logger.info(
                        "[MidLong] stage=close_match symbol=%s dec_nature=%s "
                        "pos_nature=%s → alias_ok",
                        sym, _dec_n, _cn,
                    )
                    break
        # ── 严格化：close/reduce 找不到匹配仓位则跳过，防止跨 tier 误伤 ──
        if pos is None and action in ("close", "reduce"):
            _have = [
                (p.get("trade_nature") or "?")
                for p in (symbol_positions.get(sym, []) or [])
            ]
            logger.warning(
                f"[FullAuto] {sym} {action}[{tier}/{trade_nature}] 找不到匹配仓位，"
                f"跳过执行（避免跨tier误伤）；现有仓 natures={_have}"
            )
            continue
        if pos is None:
            sym_poss = symbol_positions.get(sym, [])
            if sym_poss:
                pos = sym_poss[0]

        # ── 跨层校验：close/reduce 必须 targeting 正确的 tier/nature ──
        if pos and action in ("close", "reduce"):
            _pos_nature = (pos.get("trade_nature") or "").strip().lower()
            _pos_tier = (pos.get("timeframe_tier") or "").strip().lower()
            _dec_nature_stripped = (trade_nature or "").strip().lower()
            if _pos_nature and _dec_nature_stripped and _pos_nature != _dec_nature_stripped:
                _both_ml = (
                    _pos_nature in _MIDLONG_NATURE_ALIAS
                    and _dec_nature_stripped in _MIDLONG_NATURE_ALIAS
                )
                if _both_ml:
                    logger.info(
                        "[MidLong] stage=close_match symbol=%s 允许中长线别名 "
                        "dec=%s pos=%s[%s]",
                        sym, _dec_nature_stripped, _pos_nature, _pos_tier,
                    )
                else:
                    logger.warning(
                        f"[FullAuto] tier/nature不匹配: 决策={_dec_nature_stripped}[{tier}] "
                        f"仓位={_pos_nature}[{_pos_tier}]，跳过 {sym} {action}（防止跨tier误操作）"
                    )
                    continue

        # 事件日志：有仓位时优先展示仓位周期中文标签，否则用当前决策 tier
        pos_log_scope = host.event_scope_label(
            None,
            (pos.get("timeframe_tier") if pos else None) or tier,
        )

        # ── 整改项6: 仓位最小决策间隔 ────────────────────
        if pos:
            _pos_id = pos.get("id") or pos.get("position_id")
            _pos_tier = pos.get("timeframe_tier", "mid")

            # 止损场景豁免间隔限制
            _has_sl_trigger = False
            _sl_price = pos.get("sl_price")
            _mark_price = pos.get("mark_price") or pos.get("current_price")
            if _sl_price and _mark_price:
                _pos_side = pos.get("side", "long")
                if _pos_side == "long" and float(_mark_price) <= float(_sl_price):
                    _has_sl_trigger = True
                elif _pos_side == "short" and float(_mark_price) >= float(_sl_price):
                    _has_sl_trigger = True

            if _pos_id and not _has_sl_trigger and not host.should_evaluate_position(_pos_id, _pos_tier):
                _remaining = host.position_min_decision_interval.get(_pos_tier, 600) - \
                    (time.time() - host.position_last_decision_ts.get(_pos_id, 0))
                host.append_event(session, "decision_interval",
                    f"⏱️ {sym}[{pos_log_scope}] 决策间隔内(剩余{_remaining:.0f}s)，跳过本轮评估")
                continue

        # ── AI 动态 TP/SL 调整（任何 action 包括 hold 都可以触发）──
        # 含周期最小距离校验：防止 AI 把不同周期的 TP/SL 拉到同一水平
        if pos:
            _adj_tp = dec.get("adjust_tp")
            _adj_sl = dec.get("adjust_sl")
            if _adj_tp is not None or _adj_sl is not None:
                try:
                    tp_new = float(_adj_tp) if _adj_tp else None
                    sl_new = float(_adj_sl) if _adj_sl else None
                    if tp_new and tp_new > 0:
                        tp_new = round(tp_new, 6)
                    else:
                        tp_new = None
                    if sl_new and sl_new > 0:
                        sl_new = round(sl_new, 6)
                    else:
                        sl_new = None

                    entry_p = float(pos.get("entry_price", 0) or 0)
                    pos_side = pos.get("side", "long")
                    if tp_new or sl_new:
                        tp_new, sl_new = host.validate_tp_sl_by_nature(
                            trade_nature, pos_side, entry_p, tp_new, sl_new, sym)

                    # 硬性最小 SL 距离保护（与 paper_engine._MIN_SL_DISTANCE_BY_NATURE 对齐）
                    _MIN_SL = {"scalp": 0.025, "intraday": 0.035,
                               "swing": 0.045, "position": 0.055,
                               "trend_follow": 0.065}
                    if sl_new and entry_p > 0:
                        _min_d = _MIN_SL.get(trade_nature, 0.025)
                        if pos_side in ("long", "buy"):
                            _floor = round(entry_p * (1 - _min_d), 6)
                            if sl_new > _floor:
                                sl_new = _floor
                        else:
                            _floor = round(entry_p * (1 + _min_d), 6)
                            if sl_new < _floor:
                                sl_new = _floor

                    if tp_new or sl_new:
                        pos_id = pos.get("id")
                        result = paper_engine.update_position_tp_sl(
                            db, pos_id, tp_price=tp_new, sl_price=sl_new)
                        if result:
                            parts = []
                            if tp_new:
                                parts.append(f"TP→${tp_new:.4f}")
                            if sl_new:
                                parts.append(f"SL→${sl_new:.4f}")
                            host.append_event(session, "ai_tp_sl_adjust",
                                f"🎯 AI调整 {sym}[{pos_log_scope}] {' '.join(parts)} | {reasoning}")
                            logger.info(
                                f"[FullAuto] AI TP/SL调整 {sym}[{pos_log_scope}]: "
                                f"tp={tp_new} sl={sl_new}")
                except Exception as adj_err:
                    logger.warning(f"[FullAuto] AI TP/SL调整异常 {sym}: {adj_err}")

        # ── AI 延长持仓（仅 mid/long；短线 scalp/intraday 禁止续命）──
        _extend_h = dec.get("extend_hold_hours")
        if pos and _extend_h is not None:
            try:
                from backend.services.position_hold_time import is_short_no_ai_hold_nature
                _pos_nature = str(pos.get("trade_nature") or "").strip().lower()
                if is_short_no_ai_hold_nature(_pos_nature):
                    logger.info(
                        "[FullAuto] 跳过AI延长 %s: 短线(%s)禁止续命",
                        sym, _pos_nature,
                    )
                else:
                    _add_h = float(_extend_h)
                    if _add_h > 0:
                        _ext = paper_engine.extend_position_hold_hours(
                            db, int(pos.get("id")), _add_h,
                            reason=f"ai_extend:{reasoning[:80]}",
                        )
                        if _ext:
                            host.append_event(
                                session, "ai_extend_hold",
                                f"⏳ AI延长 {sym}[{pos_log_scope}] "
                                f"{_ext['before_max_hours']:.1f}h→{_ext['after_max_hours']:.1f}h "
                                f"(+{_ext['added_hours']:.1f}h) | {reasoning[:120]}",
                            )
                            host.clear_hold_timeout_queue_entry(pos)
            except Exception as _ext_err:
                logger.warning(f"[FullAuto] AI延长持仓异常 {sym}: {_ext_err}")

        # ── AI 主动止盈（partial_close_pct；与 close/reduce/hold 互斥）
        # hold = 不动仓位，不应触发任何部分平仓；reduce/close 已有独立处理逻辑
        _partial_pct = dec.get("partial_close_pct")
        if _partial_pct and pos and action not in ("close", "reduce", "hold"):
            try:
                pct = int(_partial_pct)
                if 1 <= pct <= 100:
                    # Tier 2 门控：partial_close 纳入 UnifiedExitExecutor
                    try:
                        from backend.config.settings import UNIFIED_EXIT_EXECUTOR_ENABLED
                        if UNIFIED_EXIT_EXECUTOR_ENABLED:
                            from backend.services.unified_exit_executor import (
                                unified_exit_executor, ExitExecuteRequest,
                            )
                            _pc_action = "close" if pct >= 95 else "reduce"
                            _pc_req = ExitExecuteRequest(
                                db=db,
                                account_id=account_id,
                                symbol=sym,
                                action=_pc_action,
                                pos=pos,
                                exit_channel="ai_take_profit",
                                reason="ai_take_profit",
                                reasoning=reasoning or "",
                                confidence=float(confidence) if confidence else None,
                                reduce_ratio=pct / 100.0,
                                tier_level=2,
                                session=session,
                                append_event=host.append_event,
                                get_risk_score=host.get_account_risk_score,
                                tier_protection=host.TIER_PROTECTION,
                            )
                            _pc_gate = unified_exit_executor.should_block(_pc_req)
                            if _pc_gate.blocked:
                                if _pc_gate.event_type and session:
                                    host.append_event(
                                        session, _pc_gate.event_type, _pc_gate.detail,
                                    )
                                continue
                    except Exception as _pc_gate_err:
                        logger.debug(f"[UnifiedExit] partial_close 门控跳过: {_pc_gate_err}")

                    # ── 累计部分平仓安全网：防止"千刀万剐" ──
                    _tracker_key = f"{session.id}:{sym}:{pos.get('strategy_id', '')}"
                    _tracker = host.partial_close_tracker.get(_tracker_key, {"total_pct": 0, "count": 0})
                    _cumulative = _tracker["total_pct"] + pct
                    if _cumulative > 80:
                        logger.info(
                            f"[FullAuto] {sym} 累计部分平仓已达 {_tracker['total_pct']}%, "
                            f"本次+{pct}%={_cumulative}% 超过80%上限，跳过（策略应全平或不动）"
                        )
                        continue
                    size = float(pos.get("size", 0) or pos.get("quantity", 0))
                    mark = float(pos.get("mark_price", 0) or pos.get("entry_price", 1))
                    close_qty = size * (pct / 100.0)
                    remaining_notional = (size - close_qty) * mark
                    side = pos.get("side", "")
                    pos_strategy_id = pos.get("strategy_id")

                    _min_notional = max(5, total_equity * 0.05)
                    if remaining_notional < _min_notional or pct >= 95:
                        result = paper_engine.close_position(
                            db, account_id, sym, side,
                            reason="ai_take_profit",
                            strategy_id=pos_strategy_id)
                        if result:
                            pnl = result.get("pnl", 0)
                            _reason = "ai_take_profit" if pnl >= 0 else "ai_cut_loss"
                            _emoji = "💰" if pnl >= 0 else "✂️"
                            _label = "AI止盈全平" if pnl >= 0 else "AI止损全平"
                            result["close_reason"] = _reason
                            session.total_trades = (session.total_trades or 0) + 1
                            host.append_event(session, _reason,
                                f"{_emoji} {_label} {sym}[{pos_log_scope}] {side} "
                                f"PnL=${pnl:+.2f} | {reasoning}")
                            # 全平后清除追踪器
                            host.partial_close_tracker.pop(_tracker_key, None)
                            _position_dirty = True
                    else:
                        result = paper_engine.close_position(
                            db, account_id, sym, side,
                            quantity=close_qty,
                            reason="ai_take_profit",
                            strategy_id=pos_strategy_id)
                        if result:
                            pnl = result.get("pnl", 0)
                            _reason = "ai_take_profit" if pnl >= 0 else "ai_cut_loss"
                            _emoji = "💰" if pnl >= 0 else "✂️"
                            _label = f"AI止盈{pct}%" if pnl >= 0 else f"AI止损{pct}%"
                            result["close_reason"] = _reason
                            session.total_trades = (session.total_trades or 0) + 1
                            host.append_event(session, _reason,
                                f"{_emoji} {_label} {sym}[{pos_log_scope}] {side} "
                                f"PnL=${pnl:+.2f} | {reasoning}")
                            # 更新累计追踪器
                            _tracker["total_pct"] = _cumulative
                            _tracker["count"] = _tracker.get("count", 0) + 1
                            host.partial_close_tracker[_tracker_key] = _tracker
                            _position_dirty = True
            except Exception as tp_err:
                logger.warning(f"[FullAuto] AI主动止盈异常 {sym}: {tp_err}")

        if action == "hold":
            # ── 编排器三周期一致性覆盖：LLM 说 hold，但编排器三时间框架全部
            #    一致看多/看空（无冲突）且置信度 ≥ 60%，且无同 symbol 持仓，直接开仓 ──
            # 谎言 1 修复（2026-05-08）：
            #   该机制此前默认开启，导致 14.1% 的 LLM "hold" 被规则系统强行覆盖成
            #   buy/sell（写库时 ai_reasoning 还保留 LLM 原始的"选择 hold"，造成"action 与
            #   reasoning 矛盾"的 1520 条决策）。现在改为默认关闭，需显式
            #   ENABLE_ORCHESTRATOR_OVERRIDE=true 才生效。
            # 2026-06-21 修复：新增 trade_nature=="scalp" 限定 + V5 门控 + 因子约束，
            #   仅对短线 scalp 层生效，不影响 swing/trend 决策流程。
            import os as _os_for_override_flag
            _orch_override_enabled = (
                _os_for_override_flag.getenv("ENABLE_ORCHESTRATOR_OVERRIDE", "false").lower()
                in ("true", "1", "yes")
            )
            _did_override = False
            # [2026-06-21] 仅对 scalp 短线层生效，不影响 swing/trend
            _overridable_nature = (trade_nature or "").strip().lower() == "scalp"
            # [2026-06-21] 若 ORCHESTRATOR_HARD_GATE 已将此决策拦截为 hold，不再回推
            _orch_already_blocked = bool(dec.get("_orch_wait_blocked"))
            if mode == "running" and not pos and _orch_override_enabled and _overridable_nature and not _orch_already_blocked:
                try:
                    _mkt_h = (market_summary or {}).get(sym, {}) if isinstance(market_summary, dict) else {}
                    _orch_h = _mkt_h.get("orchestrator", {}) if isinstance(_mkt_h, dict) else {}
                    if isinstance(_orch_h, dict) and _orch_h:
                        _l_bias = str(_orch_h.get("long_bias", "") or "")
                        _m_bias = str(_orch_h.get("mid_bias", "") or "")
                        _s_bias = str(_orch_h.get("short_bias", "") or "")
                        _lc = float(_orch_h.get("long_confidence", 0) or 0)
                        _mc = float(_orch_h.get("mid_confidence", 0) or 0)
                        _sc = float(_orch_h.get("short_confidence", 0) or 0)
                        # 修复：取所有对齐周期的置信度最大值，避免nature推tier选到低置信周期
                        # （override 条件要求 mid+short 至少同向，故取对齐者max最合理）
                        _rec_nature = str(_orch_h.get("recommended_nature", "") or "")
                        _rec_tier = host.nature_to_tier_map.get(_rec_nature, "mid")
                        _tier_conf_map = {"long": _lc, "mid": _mc, "short": _sc}
                        _aligned_confs = []
                        if _m_bias in ("bullish", "bearish"): _aligned_confs.append(_mc)
                        if _s_bias in ("bullish", "bearish"): _aligned_confs.append(_sc)
                        if _l_bias in ("bullish", "bearish"): _aligned_confs.append(_lc)
                        _orch_conf = (max(_aligned_confs) * 100) if _aligned_confs else _tier_conf_map.get(_rec_tier, _mc) * 100
                        _orch_action = str(_orch_h.get("action", "") or "")

                        # 三周期全一致（long 可以是 neutral —— LongTermPlanner 数据不足时常见）
                        _all_bullish = (_l_bias == "bullish" and _m_bias == "bullish"
                                        and _s_bias in ("bullish", "neutral"))
                        _all_bearish = (_l_bias == "bearish" and _m_bias == "bearish"
                                        and _s_bias in ("bearish", "neutral"))
                        # 中短期一致（允许 long 为 neutral/同向；覆盖 LongTermPlanner 数据不足场景）
                        _mid_short_bullish = (_m_bias == "bullish" and _s_bias == "bullish"
                                              and _l_bias in ("bullish", "neutral"))
                        _mid_short_bearish = (_m_bias == "bearish" and _s_bias == "bearish"
                                              and _l_bias in ("bearish", "neutral"))
                        _orch_wants_enter = _orch_action not in ("wait", "frozen", "")

                        _direction_aligned = (_all_bullish or _all_bearish
                                              or _mid_short_bullish or _mid_short_bearish)

                        if not _direction_aligned or _orch_conf < 60 or not _orch_wants_enter:
                            logger.debug(
                                f"[FullAuto] 编排器覆盖条件不满足 {sym}: "
                                f"L={_l_bias}/M={_m_bias}/S={_s_bias} "
                                f"conf={_orch_conf:.0f}% action={_orch_action} "
                                f"aligned={_direction_aligned} wants_enter={_orch_wants_enter}"
                            )

                        if (_orch_conf >= 60 and _orch_wants_enter and _direction_aligned):
                            _override_action = "buy" if (_all_bullish or _mid_short_bullish) else "sell"
                            _override_conf = max(55, min(75, int(_orch_conf)))
                            host.append_event(session, "orchestrator_override",
                                f"🔥 编排器覆盖 {sym}: LLM hold → {_override_action} "
                                f"(L={_l_bias}/M={_m_bias}/S={_s_bias} conf={_orch_conf:.0f}%)")
                            logger.info(
                                f"[FullAuto] 编排器三周期一致覆盖 {sym}: "
                                f"hold→{_override_action} "
                                f"L={_l_bias}/M={_m_bias}/S={_s_bias} conf={_orch_conf:.0f}%"
                            )
                            # 再开仓冷却检查
                            _blocked = False
                            try:
                                from backend.services.reentry_cooldown import reopen_blocked
                                # 编排器覆盖强制走 mid tier，tier-isolated 冷却
                                _b, _why = reopen_blocked(account_id, sym, _override_action, new_tier="mid")
                                if _b:
                                    host.append_event(session, "reentry_cooldown",
                                        f"⏳ 编排器覆盖被冷却阻止: {_why}")
                                    _blocked = True
                                    try:
                                        from backend.services.unified_risk_gate import record_guard_block
                                        record_guard_block(
                                            db, account_id=account_id,
                                            guard_name="reentry_cooldown",
                                            symbol=sym, side=_override_action,
                                            reason=_why,
                                            extra={"point": "orchestrator_override"},
                                        )
                                    except Exception:
                                        pass
                            except Exception:
                                pass

                            # ── [2026-06-21] V5 统一门控检查（编排器覆盖也必须过门）──
                            _gate_blocked = False
                            _gate_reason = ""
                            if not _blocked and not risk_block_new_positions:
                                try:
                                    from backend.services.decision_core.unified_gate import evaluate_entry
                                    _gate_sl_pct = 0.03
                                    _gate_tp_pct = 0.06
                                    _gate_result = evaluate_entry(
                                        db=db,
                                        account_id=account_id,
                                        symbol=sym,
                                        action=_override_action,
                                        confidence=float(_orch_conf),
                                        tier=_rec_tier,
                                        trade_nature=(trade_nature or "scalp"),
                                        tp_pct=_gate_tp_pct,
                                        sl_pct=_gate_sl_pct,
                                        market_data=_mkt_h if isinstance(_mkt_h, dict) else {},
                                        base_entry_threshold=60,
                                        mode=mode,
                                    )
                                    if not _gate_result.allowed:
                                        _gate_blocked = True
                                        _gate_reason = _gate_result.reason or "gate_blocked"
                                        host.append_event(session, "orchestrator_override",
                                            f"🚫 编排器覆盖被V5门控拦截 {sym}: {_gate_reason}")
                                        logger.info(
                                            f"[FullAuto] 编排器覆盖被V5门控拦截 {sym}: {_gate_reason}"
                                        )
                                except Exception as _gate_err:
                                    # fail-closed：门控异常与门控拒绝同等对待，不能因为
                                    # 检查过程本身出错就让编排器覆盖绕过 V5 统一门控。
                                    _gate_blocked = True
                                    _gate_reason = "gate_check_error"
                                    logger.warning(
                                        f"[FullAuto] V5门控检查异常 {sym}: {_gate_err}，按 fail-closed 拦截"
                                    )

                            # ── [2026-06-21] 因子复合信号硬否决 ──
                            _factor_veto_reason = None
                            if not _blocked and not _gate_blocked and not risk_block_new_positions:
                                try:
                                    _factor_veto_reason = host.factor_veto_check(db, sym, _override_action, mode=mode)
                                    if _factor_veto_reason:
                                        host.append_event(session, "orchestrator_override",
                                            f"🧬 编排器覆盖被因子否决 {sym}: {_factor_veto_reason}")
                                        logger.info(
                                            f"[FullAuto] 编排器覆盖被因子否决 {sym}: {_factor_veto_reason}"
                                        )
                                except Exception as _veto_err:
                                    if mode == "live":
                                        # Live 环境 fail-closed：因子否决层是硬约束的一部分，
                                        # 检查异常不能等同于"通过检查"。
                                        _factor_veto_reason = "factor_veto_check_error"
                                        logger.warning(
                                            f"[FullAuto] 因子否决检查异常 {sym}: {_veto_err}，Live 环境按 fail-closed 否决"
                                        )
                                    else:
                                        logger.warning(f"[FullAuto] 因子否决检查异常 {sym}: {_veto_err}（Paper 环境保持现状不否决）")

                            if not _blocked and not _gate_blocked and _factor_veto_reason is None and not risk_block_new_positions:
                                _mkt2 = (market_summary or {}).get(sym, {}) if isinstance(market_summary, dict) else {}
                                _orch2 = _mkt2.get("orchestrator", {}) if isinstance(_mkt2, dict) else {}
                                # 杠杆由置信度驱动，不从 orchestrator 统一值取
                                _conf_pct = int(_override_conf * 100) if _override_conf else 50
                                if _conf_pct >= 90: _dyn_lev = 20
                                elif _conf_pct >= 80: _dyn_lev = 18
                                elif _conf_pct >= 70: _dyn_lev = 15
                                elif _conf_pct >= 60: _dyn_lev = 12
                                elif _conf_pct >= 50: _dyn_lev = 10
                                elif _conf_pct >= 40: _dyn_lev = 8
                                elif _conf_pct >= 30: _dyn_lev = 6
                                else: _dyn_lev = 5
                                _vol2 = float(_mkt2.get("volatility_value", 0.015) or 0.015) if isinstance(_mkt2, dict) else 0.015
                                _regime2 = _mkt2.get("market_cycle", "unknown") if isinstance(_mkt2, dict) else "unknown"
                                _pos_pct_base = host.ai_dynamic_position_pct(
                                    _override_conf, _vol2, len(positions_list or []),
                                    tier=tier)
                                # v3 整改: Kelly 上限夹紧 + DRL 影子建议（透传安全）
                                _pos_pct, _tdi_meta_ov = host.apply_tdi_position_advice(
                                    symbol=sym,
                                    base_pct=_pos_pct_base,
                                    confidence=_override_conf,
                                    volatility=_vol2,
                                    open_position_count=len(positions_list or []),
                                    tier=tier,
                                    equity=float(total_equity or 0.0),
                                    regime=_regime2 if isinstance(_regime2, str) else "ranging",
                                    base_direction=("long" if _override_action == "buy" else ("short" if _override_action == "sell" else "hold")),
                                )
                                _ref_p2 = 0.0
                                try:
                                    from backend.services.market_data import get_last_price
                                    _ref_p2 = get_last_price(sym) or 0
                                except Exception:
                                    pass
                                _sl2, _tp2 = 0.0, 0.0
                                # [2026-06-21] override SL/TP 改为从 TIER_TP_SL_DEFAULTS 读取
                                try:
                                    from backend.config.settings import TIER_TP_SL_DEFAULTS as _TSD
                                    _tier_sl = _TSD.get(_rec_tier, {}).get("sl_pct", 0.03)
                                    _tier_tp = _TSD.get(_rec_tier, {}).get("tp_pct", 0.06)
                                except Exception:
                                    _tier_sl = 0.025
                                    _tier_tp = 0.045
                                if _ref_p2 > 0:
                                    if _override_action == "buy":
                                        _sl2 = round(_ref_p2 * (1 - _tier_sl), 6)
                                        _tp2 = round(_ref_p2 * (1 + _tier_tp), 6)
                                    else:
                                        _sl2 = round(_ref_p2 * (1 + _tier_sl), 6)
                                        _tp2 = round(_ref_p2 * (1 - _tier_tp), 6)
                                # [fix] 防御性 rollback
                                try: db.rollback()
                                except Exception: pass
                                _strat_list2 = db.query(_AIStrategy).filter(
                                    _AIStrategy.strategy_id.in_(active_ids),
                                    _AIStrategy.primary_symbol == sym,
                                    _AIStrategy.status == "active",
                                ).all()
                                # 优先选 mid tier 策略，避免落到 long 策略导致 tier 塌缩
                                _picked_strat = None
                                if _strat_list2:
                                    for _s in _strat_list2:
                                        if (getattr(_s, "timeframe_tier", "") or "").strip().lower() == "mid":
                                            _picked_strat = _s
                                            break
                                    if _picked_strat is None:
                                        for _s in _strat_list2:
                                            if (getattr(_s, "timeframe_tier", "") or "").strip().lower() == "short":
                                                _picked_strat = _s
                                                break
                                    if _picked_strat is None:
                                        _picked_strat = _strat_list2[0]
                                if _picked_strat is not None and _ref_p2 > 0:
                                    _picked_strat = host.ensure_bound_strategy(
                                        db, _picked_strat,
                                        active_ids=active_ids,
                                        symbol=sym,
                                        status=("active",),
                                    )
                                    if _picked_strat is None:
                                        continue
                                    # 对齐 decision 的 tier 与 nature
                                    _ov_tier = (getattr(_picked_strat, "timeframe_tier", "") or "mid").strip().lower()
                                    if _ov_tier not in ("short", "mid", "long"):
                                        _ov_tier = "mid"
                                    from backend.services.sub_position_manager import TIER_TO_NATURE as _TIER_TO_NATURE_OV
                                    _ov_nature = _TIER_TO_NATURE_OV.get(_ov_tier, "swing")
                                    _dd2 = {
                                        "action": _override_action, "side": _override_action,
                                        "price": _ref_p2, "leverage": _dyn_lev,
                                        "position_pct": _pos_pct,
                                        "confidence_pct": _override_conf,
                                        "stop_loss_price": _sl2,
                                        "take_profit_price": _tp2,
                                        "market_regime": _regime2,
                                        "volatility_pct": _vol2,
                                        "timeframe_tier": _ov_tier,
                                        "trade_nature": _ov_nature,
                                        "_tdi_meta": _tdi_meta_ov,
                                    }
                                    logger.info(
                                        f"[FullAuto] 编排器覆盖选中策略 {getattr(_picked_strat,'name','?')} "
                                        f"tier={_ov_tier} nature={_ov_nature}"
                                    )
                                    _ov_ok = host.execute_paper_trade(db, session, _picked_strat, _dd2)
                                    if _ov_ok:
                                        _position_dirty = True
                                        session.total_trades = (session.total_trades or 0) + 1
                                        host.mark_master_decision_executed(
                                            _snap_entry, _dec_log_entry, db,
                                            operation=_override_action,
                                        )
                                        _did_override = True
                except Exception as _ov_err:
                    logger.warning(f"[FullAuto] 编排器覆盖异常 {sym}: {_ov_err}", exc_info=True)
            if not _did_override:
                continue
            else:
                continue  # 已执行开仓，跳过后续 buy/sell 重复处理

        # ══════════════════════════════════════════════════
        # 编排器 frozen 约束：frozen 状态下阻止新开仓，但不阻止止损
        # 修复：此前亏损<8%时阻止close/reduce，在快速下跌中扩大亏损
        # 新逻辑：frozen 只阻止 buy/sell/pyramid/dca，close/reduce 始终放行
        # ══════════════════════════════════════════════════
        if action in ("buy", "sell", "pyramid", "dca") and not pos:
            # M6: 因子复合信号反向硬否决（Agent 独立 mid/long 路径跳过）
            if action in ("buy", "sell") and not dec.get("_agent_independent"):
                _veto_reason = host.factor_veto_check(db, sym, action, mode=mode)
                if _veto_reason:
                    host.append_event(session, "factor_veto",
                        f"⛔ {sym} {action} 因子否决: {_veto_reason[:120]}")
                    logger.info(f"[FullAuto] 因子否决(主路径) {sym}: {_veto_reason}")
                    try:
                        from backend.services.decision_core.unified_gate import (
                            record_block_event,
                        )
                        record_block_event(sym, action, "factor_veto", _veto_reason)
                    except Exception:
                        pass
                    continue

            # 趋势仓：必须有 K 线 LLM 深度结论（非规则回退）
            try:
                from backend.config.settings import (
                    KLINE_ANALYST_MODE,
                    TREND_REQUIRES_KLINE_DEEP,
                )
                from backend.services.trading_analysts import kline_deep_signal_ready
                _need_kline = (
                    TREND_REQUIRES_KLINE_DEEP
                    and KLINE_ANALYST_MODE in ("all", "rotate")
                    and (trade_nature or "") in ("trend_follow", "position")
                    and action in ("buy", "sell")
                )
                if _need_kline:
                    _k_ok, _k_why = kline_deep_signal_ready(analyst_reports, sym)
                    if not _k_ok:
                        host.append_event(
                            session, "kline_deep_required",
                            f"⛔ {sym} 趋势仓拦截: 缺少K线深度分析 | {_k_why[:100]}",
                        )
                        logger.info(
                            f"[FullAuto] 趋势仓K线深度门控 {sym} {action}: {_k_why}"
                        )
                        try:
                            from backend.services.decision_core.unified_gate import (
                                record_block_event,
                            )
                            record_block_event(
                                sym, action, "kline_deep_required", _k_why[:200],
                            )
                        except Exception:
                            pass
                        continue
            except Exception as _kline_gate_err:
                logger.debug(f"[FullAuto] K线深度门控跳过: {_kline_gate_err}")

            # V5 / MidLong 组合门控
            try:
                _v5_dec = dict(dec)
                _v5_dec.setdefault("action", action)
                _v5_dec.setdefault("confidence", float(confidence or 0))
                _v5_dec.setdefault("timeframe_tier", tier or "mid")
                _v5_dec.setdefault("trade_nature", trade_nature or "swing")
                # [阶段0 修复] auto-coin 判断改用正向白名单 fresh DB 查询，避免 ORM
                # 快照 getattr(session,"auto_coin_symbols") 在长 tick 内漏入新注入的
                # auto-coin。is_auto_coin 语义：不在固定白名单 = auto-coin 或非会话币。
                from backend.services.auto_coin_selector import get_fixed_symbols_for_session
                from backend.services.auto_coin_policy import is_training_core_symbol
                _fixed_syms_v5 = get_fixed_symbols_for_session(session.session_id, db, tier="long")
                # 训练核心币永远不算 auto-coin（保留原 applies_strict_auto_coin_rules 语义）
                _is_auto_coin_fresh = (
                    sym.upper() not in _fixed_syms_v5
                    and not is_training_core_symbol(sym)
                )
                _mkt_sym = (market_summary or {}).get(sym) if isinstance(market_summary, dict) else None
                _mode_l = (getattr(session, "trading_mode", None) or "paper").strip().lower()
                if trade_nature in ("swing", "trend_follow", "position") or dec.get("_agent_independent"):
                    from backend.services.decision_core.pipeline import evaluate_midlong_open
                    _v5_allowed, _v5_reason, _v5_adj = evaluate_midlong_open(
                        db=db,
                        account_id=account_id,
                        symbol=sym,
                        dec=_v5_dec,
                        market_data=_mkt_sym,
                        mode=_mode_l,
                        persistence_allow=host.midlong_persistence_allow(
                            sym, trade_nature or "swing", action,
                        ),
                    )
                else:
                    from backend.services.decision_core import evaluate_open_decision
                    _v5_allowed, _v5_reason, _v5_adj = evaluate_open_decision(
                        db=db,
                        account_id=account_id,
                        symbol=sym,
                        dec=_v5_dec,
                        market_data=_mkt_sym,
                        base_entry_threshold=50 + int(dec.get("_dcp_penalty") or 0),
                        is_auto_coin=_is_auto_coin_fresh,
                        mode=_mode_l,
                    )
                if not _v5_allowed:
                    host.append_event(session, "v5_unified_gate",
                        f"⛔ {sym} {action} V5门控: {_v5_reason[:100]}")
                    try:
                        from backend.services.block_report_aggregator import record_block
                        record_block("v5_unified_gate", f"{sym} {action}: {_v5_reason[:100]}")
                    except Exception:
                        pass
                    continue
                if (_v5_adj or {}).get("size_multiplier") and float(_v5_adj["size_multiplier"]) < 0.999:
                    dec["size_multiplier"] = float(_v5_adj["size_multiplier"])
            except Exception as _v5_err:
                logger.warning(f"[FullAuto] V5 unified gate 异常（fail-closed）: {_v5_err}")
                host.append_event(session, "v5_gate_error",
                    f"⛔ {sym} {action} V5门控异常拦截: {str(_v5_err)[:80]}")
                continue

            try:
                from backend.config.settings import get_orchestrator_hard_gate
                if get_orchestrator_hard_gate(mode):
                    _mkt_fz = (market_summary or {}).get(sym, {})
                    _orch_fz = _mkt_fz.get("orchestrator", {}) if isinstance(_mkt_fz, dict) else {}
                    if isinstance(_orch_fz, dict) and _orch_fz.get("action") == "frozen":
                        host.append_event(session, "orchestrator_frozen_block",
                            f"🔒 编排器冻结拦截 {sym} {action}: frozen 状态，不允许新开仓 | "
                            f"{str(_orch_fz.get('reasoning', ''))[:60]}")
                        logger.info(
                            f"[FullAuto] 编排器冻结拦截 {sym} {action}: orch=frozen")
                        continue
            except Exception:
                pass

        # 持仓超时：不在此强制改 hold→close，由 hold_timeout_review_queue + LLM 复审决定

        # ══════════════════════════════════════════════════
        # 精简保护层（v3）：仅保留 3 层核心保护，让 AI 决策充分执行
        # 层 A: 新仓保护期（防自杀式操作）
        # 层 B: SubPositionManager 冷却审核（per-nature 冷却+利润门槛）
        # 层 C: 仓位过小保护（防无限切割）
        # 其他保护由 TP/SL 机制和 AI 提示词约束，不再在此重复拦截
        # ══════════════════════════════════════════════════

        _legacy_exit_gates = True
        if action in ("close", "reduce") and pos:
            try:
                from backend.config.settings import UNIFIED_EXIT_EXECUTOR_ENABLED
                if UNIFIED_EXIT_EXECUTOR_ENABLED:
                    from backend.services.unified_exit_executor import (
                        unified_exit_executor, ExitExecuteRequest,
                    )
                    _legacy_exit_gates = False
                    _ht_syms = getattr(session, "_hold_timeout_review_symbols", None) or set()
                    _is_ht_review = sym.upper() in _ht_syms
                    _exit_ch = (
                        "hold_timeout_review" if _is_ht_review
                        else f"master_{mode}_{action}"
                    )
                    _exit_tier = 1 if _is_ht_review else 2
                    _exit_req = ExitExecuteRequest(
                        db=db,
                        account_id=account_id,
                        symbol=sym,
                        action=action,
                        pos=pos,
                        exit_channel=_exit_ch,
                        reason=f"master_{mode}",
                        reasoning=reasoning or "",
                        confidence=float(confidence) if confidence else None,
                        tier_level=_exit_tier,
                        mode=mode,
                        session=session,
                        append_event=host.append_event,
                        get_risk_score=host.get_account_risk_score,
                        tier_protection=host.TIER_PROTECTION,
                    )
                    _gate = unified_exit_executor.should_block(_exit_req)
                    if _gate.blocked:
                        if _gate.convert_to_set_sl:
                            unified_exit_executor._set_emergency_sl(_exit_req)
                        continue
                    # 门控已通过 → 统一执行器执行，禁止 fall-through 到 legacy 裸 close
                    _ux_result = unified_exit_executor.execute(_exit_req)
                    if _ux_result is not None:
                        pnl = _ux_result.get("pnl", 0)
                        session.total_trades = (session.total_trades or 0) + 1
                        _pos_tier_close = pos.get("timeframe_tier", "mid")
                        _pls = host.event_scope_label(None, _pos_tier_close)
                        event_type = "defensive_close" if mode == "defensive" else "trade_executed"
                        _tier_label = {"short": "短线", "mid": "中线", "long": "长线"}.get(
                            _pos_tier_close, _pos_tier_close)
                        _act_cn = "平仓" if action == "close" else "减仓"
                        host.append_event(
                            session, event_type,
                            f"{'🛡️ 防守' if mode == 'defensive' else f'📊 {_tier_label}'}{_act_cn} "
                            f"{sym}[{_pls}] {pos.get('side', '')} PnL=${pnl:+.2f} | {reasoning}",
                        )
                        host.clear_hold_timeout_queue_entry(pos)
                        _position_dirty = True
                        host.mark_master_decision_executed(_snap_entry, _dec_log_entry, db)
                    continue
            except Exception as _uex_err:
                logger.warning(f"[UnifiedExit] 门控异常(拦截): {_uex_err}")
                _legacy_exit_gates = True

        # ── 保护层 A: 新仓保护期 — 刚开仓不允许立即 reduce/close ──
        if _legacy_exit_gates and action in ("close", "reduce") and pos:
            tier = pos.get("timeframe_tier", "mid")
            pos_log_scope = host.event_scope_label(None, tier)
            tier_cfg = host.TIER_PROTECTION.get(tier, host.DEFAULT_PROTECTION)
            protect_min = tier_cfg["protect_min"]
            emergency_pct = tier_cfg["emergency_pct"]
            tier_label = {"short": "短线", "mid": "中线", "long": "长线"}.get(tier, "中线")

            opened_at_str = pos.get("opened_at") or ""
            age_minutes = None
            if opened_at_str:
                try:
                    opened_at = datetime.fromisoformat(
                        str(opened_at_str).replace("Z", "+00:00"))
                    if opened_at.tzinfo is None:
                        opened_at = opened_at.replace(tzinfo=timezone.utc)
                    age_minutes = (datetime.now(timezone.utc) - opened_at).total_seconds() / 60.0
                except (ValueError, TypeError) as e:
                    logger.debug(f"[FullAuto] 解析 opened_at 失败: {e}")

            if age_minutes is not None and age_minutes < protect_min:
                margin = float(pos.get("margin", 0))
                upnl = float(pos.get("unrealized_pnl", 0))
                pnl_pct = (upnl / margin * 100) if margin > 0 else 0

                if pnl_pct > emergency_pct:
                    host.append_event(session, "position_protected",
                        f"🛡️ {tier_label}保护 {sym}: 开仓{age_minutes:.0f}分钟"
                        f"(保护期{protect_min}分钟)，忽略{action} | "
                        f"浮盈亏{pnl_pct:+.1f}%未触及紧急阈值{emergency_pct}%")
                    continue
                else:
                    logger.warning(
                        f"[FullAuto] {tier_label}仓{sym}仅{age_minutes:.0f}分钟"
                        f"但亏损{pnl_pct:+.1f}%触及紧急阈值{emergency_pct}%，允许{action}")

        # ── 整改项2: 防守模式分层管理（波动率感知版） ────────────────────
        from backend.config.settings import DEFENSIVE_TIERED_MODE, REDUCE_MAX_COUNT as _DEF_REDUCE_MAX
        from backend.config.settings import DEFENSIVE_VOLATILITY_TIERS as _DVT
        if DEFENSIVE_TIERED_MODE and mode == "defensive" and action in ("reduce", "close") and pos:
            _margin_val = float(pos.get("margin", 0))
            _upnl_val = float(pos.get("unrealized_pnl", 0))
            _pnl_pct = (_upnl_val / _margin_val) if _margin_val > 0 else 0
            _pos_reduce_count = int(pos.get("reduce_count", 0))

            # ── 波动率感知阈值 ──
            # 根据币种波动率分档调整亏损阈值，避免高波动币种被正常波动触发减仓
            _vol_map = _DVT.get("symbol_vol_map", {})
            _vol_multipliers = _DVT.get("vol_multipliers", {})
            _base_light = _DVT.get("light_pct", 0.02)
            _base_moderate = _DVT.get("moderate_pct", 0.05)
            _base_severe = _DVT.get("severe_pct", 0.05)
            _vol_tier = _vol_map.get(sym.lower(), "mid")  # 未知币种默认中等波动
            _vol_mult = _vol_multipliers.get(_vol_tier, 1.0)
            _adj_light = _base_light * _vol_mult     # 高波动币: -5%
            _adj_moderate = _base_moderate * _vol_mult  # 高波动币: -12.5%
            _adj_severe = _base_severe * _vol_mult    # 高波动币: -12.5%

            # 已减仓>=上限次 → 强制hold，等待SL触发
            if _pos_reduce_count >= _DEF_REDUCE_MAX:
                host.append_event(session, "defensive_reduce_limit",
                    f"🛡️ {sym} 已减仓{_pos_reduce_count}次，defensive下强制hold")
                continue

            # 轻微亏损(0~-light) → 只调SL，不reduce/close
            if -_adj_light < _pnl_pct < 0:
                host.append_event(session, "defensive_light",
                    f"🛡️ {sym} 轻微亏损{_pnl_pct:.1%}[{_vol_tier}×{_vol_mult:.1f}，阈值-{_adj_light:.0%}]，收紧SL而非{action}")
                continue

            # 中度亏损(-light~-moderate) → reduce比例限制为25%（在后续reduce_ratio处生效）
            if -_adj_moderate < _pnl_pct <= -_adj_light:
                if action == "close":
                    # 中度亏损不允许close，降级为hold
                    host.append_event(session, "defensive_moderate",
                        f"🛡️ {sym} 中度亏损{_pnl_pct:.1%}[阈值-{_adj_light:.0%}~-{_adj_moderate:.0%}]，defensive下禁止close，等待SL")
                    continue
                # action == "reduce" → 放行，但后续限制 reduce_ratio ≤ 25%
                host.defensive_reduce_cap = 0.25

            # 严重亏损(<-severe) → 允许close；reduce改为hold并建议设紧急SL
            elif _pnl_pct <= -_adj_severe:
                if action == "reduce":
                    host.append_event(session, "defensive_severe",
                        f"🛡️ {sym} 深度亏损{_pnl_pct:.1%}[{_vol_tier}×{_vol_mult:.1f}，阈值-{_adj_severe:.0%}]，应设紧急SL而非逐步减仓")
                    continue
                # action == "close" 正常放行
            else:
                # _pnl_pct >= 0 (盈利仓位) → defensive下不应reduce/close盈利仓
                if _pnl_pct >= 0:
                    host.append_event(session, "defensive_profit_hold",
                        f"🛡️ {sym} 盈利{_pnl_pct:.1%}，defensive下无需{action}")
                    continue

        # ══════════════════════════════════════════════════════════════
        # P3 M1 — MasterController close/reduce 硬事实门控
        #
        # 取代旧的 "基于 reasoning 关键词匹配的 long 免疫"（4082-4091 旧逻辑）。
        # 新门控按 tier 差异化阈值 + SL 穿透 + risk_score + reason 白名单判断，
        # 比关键词匹配客观得多。long tier 的保护强度在 MASTER_CLOSE_MIN_LOSS_PCT_BY_TIER
        # 里天然体现（long=7% 浮亏阈值 vs short=2%）。
        #
        # flag: RISK_P3_MASTER_CLOSE_REQUIRES_HARDFACT = off|shadow|enforce
        #   off     → 短路
        #   shadow  → 记日志不拦截（批次 P3-B）
        #   enforce → 真拦截（批次 P3-C）
        # ══════════════════════════════════════════════════════════════
        # [2026-07-23 清创] P3.M1 死块已删(被 unified_exit_executor 取代,
        # UNIFIED_EXIT_EXECUTOR_ENABLED=true 时 _legacy_exit_gates=False 永不进入)。

        # ── 保护层 B0: 全局 symbol 级 reduce 冷却（30分钟间隔）──
        if action == "reduce" and pos:
            import time as _time_mod
            _reduce_key = f"{account_id}:{sym}"
            _last_rt = host.last_reduce_time.get(_reduce_key, 0)
            _since_last = _time_mod.time() - _last_rt
            _REDUCE_GLOBAL_CD = 1800  # 30 分钟
            if _since_last < _REDUCE_GLOBAL_CD:
                _remain = int((_REDUCE_GLOBAL_CD - _since_last) / 60)
                logger.info(f"[FullAuto] reduce全局冷却 {sym}: 距上次{_since_last/60:.0f}分钟<30分钟")
                host.append_event(session, "reduce_global_cd",
                    f"⏳ reduce全局冷却 {sym}: 还需等{_remain}分钟")
                continue

        # ── 保护层 B: SubPositionManager 冷却审核（reduce 专用）──
        # 内含 per-nature 冷却: trend_follow=24h, swing=6h, intraday=1h
        # 内含最小利润要求: trend_follow需5%盈利, swing需2%
        # 内含手续费门卫和单次比例上限
        if action == "reduce" and pos:
            _pos_id_for_review = pos.get("id")
            _is_stop_loss = (confidence >= 80 and float(pos.get("unrealized_pnl", 0)) < 0)
            if host.sub_mgr and _pos_id_for_review:
                try:
                    _reduce_ok, _reduce_reason = host.sub_mgr.review_reduce(
                        db=db, position_id=_pos_id_for_review,
                        reduce_pct=0.5, is_stop_loss=_is_stop_loss,
                    )
                    if not _reduce_ok:
                        host.append_event(session, "reduce_cooldown",
                            f"⏳ 子仓审核拦截减仓 {sym}[{trade_nature}]: {_reduce_reason}")
                        logger.info(
                            f"[FullAuto] 子仓审核拦截 reduce {sym}[{trade_nature}]: {_reduce_reason}")
                        continue
                except Exception as _rev_err:
                    logger.warning(f"[FullAuto] review_reduce 异常(拦截): {_rev_err}")
                    host.append_event(session, "review_reduce_error",
                        f"子仓审核异常，拦截 reduce {sym}: {_rev_err}")
                    continue

        # v6: close 铁门槛（数据驱动修正版，2026-04-21）
        # ─────────────────────────────────────────────────────────
        # 数据铁证（14天 455笔）:
        #   · 被动路径 tp/profit_lock 100% 胜率 (+$79)
        #   · 主动路径 master_running 4.8% 胜率 (-$62) ← 元凶
        #   原因: LLM 把 prompt 里"亏损>8%应close"单条件触发，
        #         在 SL 到位前抢先割肉，且自报 confidence 虚高
        #         无法作为门槛依据（历史 42 笔 0.9+ 信心度胜率仍 4.8%）
        # 新规则:
        #   规则1: 仓位有 SL 且 SL 未被深度穿透(<150%) → 永远禁止 AI close
        #   规则2: SL 已被穿透 ≥150% → 允许 AI 紧急 close（SL 已失效）
        #   规则3: 无 SL 且亏 < 15% → 设紧急 SL 代替 close（保留原逻辑）
        #   规则4: 无 SL 且亏 ≥ 15% → 允许 close（保留原逻辑）
        # 预期效果: 关闭 master_running 路径 -$62/14d 的亏损出血点
        if action == "close" and pos:
            side = pos.get("side", "")
            pos_strategy_id = pos.get("strategy_id")
            if not _legacy_exit_gates:
                _pos_tier_close = pos.get("timeframe_tier", "mid")
                _pls = host.event_scope_label(None, _pos_tier_close)
                result = paper_engine.close_position(db, account_id, sym, side,
                    reason=f"master_{mode}", strategy_id=pos_strategy_id)
                if result:
                    pnl = result.get("pnl", 0)
                    session.total_trades = (session.total_trades or 0) + 1
                    event_type = "defensive_close" if mode == "defensive" else "trade_executed"
                    _tier_label = {"short":"短线","mid":"中线","long":"长线"}.get(_pos_tier_close, _pos_tier_close)
                    host.append_event(session, event_type,
                        f"{'🛡️ 防守' if mode == 'defensive' else f'📊 {_tier_label}'}平仓 "
                        f"{sym}[{_pls}] {side} PnL=${pnl:+.2f} | {reasoning}")
                    host.clear_hold_timeout_queue_entry(pos)
                    _position_dirty = True
                continue
            _has_sl = bool(pos.get("sl_price") or pos.get("stop_loss"))
            _sl_price = float(pos.get("sl_price") or pos.get("stop_loss") or 0)
            _entry = float(pos.get("entry_price", 0) or 0)
            _mark = float(pos.get("mark_price", 0) or _entry)
            _margin = float(pos.get("margin", 0) or 1)
            _upnl = float(pos.get("unrealized_pnl", 0))
            _loss_pct = abs(_upnl / _margin * 100) if _margin > 0 else 0
            _is_losing = _upnl < 0

            # 计算 SL 穿透率：当前价格到入场价的距离 / SL 到入场价的距离
            # >=1.0 表示价格已经到或超过 SL 位；>=1.5 表示深度穿透（SL 已失效）
            _sl_breach_ratio = 0.0
            if _has_sl and _entry > 0 and _sl_price > 0 and _is_losing:
                sl_distance = abs(_entry - _sl_price)
                cur_distance = abs(_entry - _mark)
                if sl_distance > 0:
                    _sl_breach_ratio = cur_distance / sl_distance

            # P0-1 修复: SL门分级拦截
            #   - 硬拦截: loss<5% 且 breach<0.5 → 让SL管理
            #   - 软警告: loss 5-10% 或 breach 0.5-1.0 → AI可接管
            #   - 允许: loss>10% 或 breach>=1.0 → 无条件AI close
            # 亏损≥2.5%（保证金）允许 AI 主动止损，避免「小亏死扛到大亏」
            _sl_hard_block = _has_sl and _sl_breach_ratio < 0.5 and _loss_pct < 2.5
            _sl_soft_bypass = _has_sl and not _sl_hard_block and _sl_breach_ratio < 1.0 and _loss_pct < 10.0
            if _sl_hard_block:
                logger.info(
                    f"[FullAuto] close 被SL门硬拦截 {sym}: "
                    f"loss={_loss_pct:.1f}%, breach={_sl_breach_ratio:.2f}<0.5, "
                    f"让SL自动管理 | ai_conf={confidence}"
                )
                host.append_event(session, "close_blocked_by_sl",
                    f"🔒 {sym} 有SL保护(穿透{_sl_breach_ratio:.2f}<0.5)，AI禁止close，"
                    f"让SL管理 | 亏{_loss_pct:.1f}% | {reasoning[:50]}")
                continue
            if _sl_soft_bypass:
                logger.info(
                    f"[FullAuto] close SL软拦截放行 {sym}: "
                    f"loss={_loss_pct:.1f}%, breach={_sl_breach_ratio:.2f}, "
                    f"允许AI接管 | ai_conf={confidence}"
                )
                host.append_event(session, "close_sl_soft_bypass",
                    f"⚠️ {sym} SL未完全穿透(breach={_sl_breach_ratio:.2f})，但亏损{_loss_pct:.1f}%，AI可接管close")
                # 不 continue，继续执行后续 close 逻辑

                            # 深度穿透/大亏场景：SL 已无法兜底，允许 AI 紧急 close
            if _has_sl and _sl_breach_ratio >= 1.0:
                logger.warning(
                    f"[FullAuto] close 放行(SL失效) {sym}: "
                    f"loss={_loss_pct:.1f}%, breach={_sl_breach_ratio:.2f}≥1.0, "
                    f"SL已被穿透，允许紧急close"
                )
                host.append_event(session, "close_sl_breached",
                    f"⚠️ {sym} SL已被深度穿透({_sl_breach_ratio:.2f}x)，"
                    f"允许紧急close | 亏{_loss_pct:.1f}%")

            # 无 SL 但亏损 < 15% → 也不准 close，改为设 SL
            # 修复：紧急 SL 基于 2x ATR 而非固定 5%，避免在正常波动中被扫掉
            if not _has_sl and _is_losing and _loss_pct < 15:
                entry_p = float(pos.get("entry_price", 0))
                if entry_p > 0:
                    # 尝试从 market_summary 获取 ATR
                    _emerg_atr_pct = 0.015  # 默认 1.5%
                    try:
                        _em_mkt = (market_summary or {}).get(sym, {}) if isinstance(market_summary, dict) else {}
                        if isinstance(_em_mkt, dict):
                            _raw_atr = float(_em_mkt.get("volatility_value", 0) or 0)
                            if _raw_atr >= 0.005:
                                _emerg_atr_pct = _raw_atr
                    except Exception:
                        pass
                    _emerg_sl_dist = max(0.03, _emerg_atr_pct * 2.5)  # 至少 3%，或 2.5x ATR
                    if side in ("long", "buy"):
                        emergency_sl = round(entry_p * (1 - _emerg_sl_dist), 6)
                    else:
                        emergency_sl = round(entry_p * (1 + _emerg_sl_dist), 6)
                    pos_id = pos.get("id")
                    paper_engine.update_position_tp_sl(
                        db, pos_id, sl_price=emergency_sl)
                    logger.info(
                        f"[FullAuto] close→设SL {sym}: 无SL亏损{_loss_pct:.1f}%, "
                        f"设紧急SL={emergency_sl} 而非直接平仓")
                    host.append_event(session, "close_to_sl",
                        f"🔧 {sym}: 无SL亏{_loss_pct:.1f}%, 设紧急SL=${emergency_sl:.2f} "
                        f"代替平仓 | {reasoning[:50]}")
                    continue

            result = paper_engine.close_position(db, account_id, sym, side,
                reason=f"master_{mode}", strategy_id=pos_strategy_id)
            if result:
                pnl = result.get("pnl", 0)
                session.total_trades = (session.total_trades or 0) + 1
                event_type = "defensive_close" if mode == "defensive" else "trade_executed"
                _tier_label = {"short":"短线","mid":"中线","long":"长线"}.get(tier, tier)
                host.append_event(session, event_type,
                    f"{'🛡️ 防守' if mode == 'defensive' else f'📊 {_tier_label}'}平仓 "
                    f"{sym}[{pos_log_scope}] {side} PnL=${pnl:+.2f} | {reasoning}")
                _position_dirty = True
                host.clear_hold_timeout_queue_entry(pos)
                host.mark_master_decision_executed(_snap_entry, _dec_log_entry, db)

        elif action == "reduce" and pos:
            # ── V5.2: 盈利仓/小亏禁止 AI 减仓（master_running_reduce 胜率仅5-16%）──
            # P3.M1 已通过 SL 逼近度门控（≥60% SL 距离才允许 reduce），
            # 此处作为二道防线：保证金亏损<硬底线(10%)直接拦截
            # [2026-07-10 加强] scalp tier 更严：浮亏<20% 不允许 AI 减仓。
            # 实测 25 笔 master_running_reduce 把盈利单砍成零头（盈亏比0.36），
            # scalp 生命周期短，应让 TP/SL 自然触发，不要 AI 频繁干预。
            _red_margin = float(pos.get("margin", 0) or 0)
            _red_upnl = float(pos.get("unrealized_pnl", 0) or 0)
            if _red_upnl >= 0:
                host.append_event(session, "reduce_blocked",
                    f"🚫 {sym}[{pos_log_scope}] 盈利仓禁止reduce (upnl=${_red_upnl:+.2f})")
                continue
            from backend.config.settings import MASTER_REDUCE_MIN_LOSS_PCT
            _red_min_loss = float(MASTER_REDUCE_MIN_LOSS_PCT)
            # scalp 单要求更高亏损门槛（20%），其他 tier 保持 10%
            if tier == "short":
                _red_min_loss = max(_red_min_loss, 0.20)
            _red_loss_pct = abs(_red_upnl) / _red_margin if _red_margin > 0 else 0
            if _red_loss_pct < _red_min_loss:
                host.append_event(session, "reduce_blocked",
                    f"🚫 {sym}[{pos_log_scope}] 浮亏{_red_loss_pct:.1%}"
                    f"<{_red_min_loss:.0%}，交给SL")
                continue

            # ── 整改项1: 减仓冷却+比例限制 ────────────────────
            from backend.config.settings import ENABLE_REDUCE_COOLDOWN, REDUCE_MAX_COUNT

            if ENABLE_REDUCE_COOLDOWN:
                # 第一道门：冷却豁免检查（止损/risk_gate/紧急场景跳过冷却）
                _exempt = host.is_reduce_cooldown_exempt(pos, reasoning)

                if not _exempt:
                    # 第二道门：减仓冷却检查
                    from backend.services.reentry_cooldown import is_reduce_cooling_down
                    _pos_side = pos.get("side", "long")
                    _cooling, _cool_reason = is_reduce_cooling_down(
                        account_id, sym, _pos_side, tier
                    )
                    if _cooling:
                        host.append_event(session, "reduce_cooldown",
                            f"⏳ {sym}[{tier}] {_cool_reason}，跳过本轮reduce")
                        continue

                    # 第三道门：累计减仓次数检查
                    _reduce_count = int(pos.get("reduce_count", 0))
                    if _reduce_count >= REDUCE_MAX_COUNT:
                        host.append_event(session, "reduce_limit",
                            f"🚫 {sym} 已减仓{_reduce_count}次(上限{REDUCE_MAX_COUNT})，只允许hold/close")
                        continue

            side = pos.get("side", "")
            pos_strategy_id = pos.get("strategy_id")
            size = pos.get("size", 0) or pos.get("quantity", 0)
            mark = pos.get("mark_price", 0) or pos.get("entry_price", 1)
            notional = size * float(mark)

            _min_notional = max(5, total_equity * 0.05)
            _tiny_upnl = float(pos.get("unrealized_pnl", 0) or 0)
            if notional < _min_notional:
                from backend.config.settings import MASTER_CLOSE_TINY_DISABLED_TIERS
                if tier in MASTER_CLOSE_TINY_DISABLED_TIERS:
                    host.append_event(session, "close_tiny_hold",
                        f"⏸️ {sym}[{pos_log_scope}] {tier}波段禁止微仓全平，交SL/TP管理")
                    continue
                if _tiny_upnl >= 0:
                    host.append_event(session, "close_tiny_hold",
                        f"⏸️ {sym}[{pos_log_scope}] 微仓盈利${_tiny_upnl:+.2f}，继续持有")
                    continue
                # 微仓等效全平复核：浮亏未达 master_close 硬阈值则持有交给 SL
                # （根治 master_running_close_tiny 小亏秒平；defensive 防守保持原状）
                # [2026-06-21] defensive 模式也加入硬事实复核
                _tc_ok, _tc_detail = host.tiny_close_allowed_by_hardfact(
                    account_id, pos, reasoning)
                if not _tc_ok:
                    host.append_event(session, "close_tiny_hold",
                        f"⏸️ {sym}[{pos_log_scope}] 微仓浮亏未达平仓硬阈值，继续持有（{_tc_detail}）")
                    continue
                result = paper_engine.close_position(db, account_id, sym, side,
                    reason=f"master_{mode}_close_tiny", strategy_id=pos_strategy_id)
                if result:
                    pnl = result.get("pnl", 0)
                    session.total_trades = (session.total_trades or 0) + 1
                    event_type = "defensive_close" if mode == "defensive" else "trade_executed"
                    _tier_label_ct = {"short":"短线","mid":"中线","long":"长线"}.get(tier, tier)
                    host.append_event(session, event_type,
                        f"{'🛡️ 防守' if mode == 'defensive' else f'📊 {_tier_label_ct}'}微仓全平 "
                        f"{sym}[{pos_log_scope}] {side} (${notional:.0f}) PnL=${pnl:+.2f} | {reasoning}")
                    _position_dirty = True
                    host.mark_master_decision_executed(_snap_entry, _dec_log_entry, db)
            else:
                margin_val = float(pos.get("margin", 0))
                upnl_val = float(pos.get("unrealized_pnl", 0))
                pnl_pct = (upnl_val / margin_val * 100) if margin_val > 0 else 0

                # 保护层 C: 仓位已缩至原始的 30% 以下 → 停止减仓
                _orig_size = float(pos.get("original_size", 0) or 0)
                if _orig_size <= 0:
                    _orig_margin = float(pos.get("original_margin", 0) or margin_val)
                    _entry_p = float(pos.get("entry_price", 1) or 1)
                    _lev = float(pos.get("leverage", 1) or 1)
                    _orig_size = (_orig_margin * _lev) / _entry_p if _entry_p > 0 else size
                if _orig_size > 0 and size / _orig_size < 0.30:
                    logger.info(
                        f"[FullAuto] reduce跳过 {sym}: 仓位仅剩原始的"
                        f"{size/_orig_size:.0%}，不再减仓")
                    continue

                # 减仓比例由 AI 信心度决定（不再按亏损分段，让 AI 自己判断）
                if confidence >= 85:
                    reduce_ratio = 0.50
                elif confidence >= 70:
                    reduce_ratio = 0.35
                else:
                    reduce_ratio = 0.25

                # 整改项2: defensive分层比例上限
                _def_cap = host.defensive_reduce_cap
                if _def_cap is not None:
                    reduce_ratio = min(reduce_ratio, _def_cap)
                    host.defensive_reduce_cap = None  # 重置，防止污染下一轮

                reduce_qty = size * reduce_ratio
                remaining_notional = (size - reduce_qty) * float(mark)
                if remaining_notional < _min_notional:
                    if upnl_val >= 0:
                        host.append_event(session, "close_tiny_hold",
                            f"⏸️ {sym}[{pos_log_scope}] 减仓后微仓仍盈利，继续持有")
                        continue
                    # 同上：减仓后碎仓的等效全平也需通过 master_close 硬阈值复核
                    # [2026-06-21] defensive 模式也加入硬事实复核
                    _tc_ok2, _tc_detail2 = host.tiny_close_allowed_by_hardfact(
                        account_id, pos, reasoning)
                    if not _tc_ok2:
                        host.append_event(session, "close_tiny_hold",
                            f"⏸️ {sym}[{pos_log_scope}] 减仓后微仓浮亏未达平仓硬阈值，继续持有（{_tc_detail2}）")
                        continue
                    result = paper_engine.close_position(db, account_id, sym, side,
                        reason=f"master_{mode}_close_tiny", strategy_id=pos_strategy_id)
                else:
                    result = paper_engine.close_position(db, account_id, sym, side,
                        quantity=reduce_qty, reason=f"master_{mode}_reduce",
                        strategy_id=pos_strategy_id)
                if result:
                    pnl = result.get("pnl", 0)
                    session.total_trades = (session.total_trades or 0) + 1
                    closed_fully = result.get("closed_fully", False)
                    event_type = "defensive_reduce" if mode == "defensive" else "trade_executed"
                    _tier_label_rd = {"short":"短线","mid":"中线","long":"长线"}.get(tier, tier)
                    label = "🛡️ 防守" if mode == "defensive" else f"📊 {_tier_label_rd}"
                    act_desc = "全平" if closed_fully else f"减仓{reduce_ratio:.0%}"
                    host.append_event(session, event_type,
                        f"{label}{act_desc} {sym}[{pos_log_scope}] {side} PnL=${pnl:+.2f} | {reasoning}")
                    import time as _time_mod2
                    _reduce_key2 = f"{account_id}:{sym}"
                    host.last_reduce_time[_reduce_key2] = _time_mod2.time()
                    host.last_reduce_time[sym] = datetime.now(timezone.utc)  # 兼容旧代码
                    _position_dirty = True
                    # 更新子仓位 reduce_count / last_reduce_at
                    if _pos_id:
                        try:
                            from backend.database.models import PaperPosition as _PP
                            _rpos = db.query(_PP).filter(_PP.id == _pos_id).first()
                            if _rpos and hasattr(_rpos, "reduce_count"):
                                _rpos.reduce_count = (_rpos.reduce_count or 0) + 1
                                _rpos.last_reduce_at = datetime.now(timezone.utc)
                                db.flush()
                        except Exception as _dbf_err:
                            logger.warning(f"[FullAuto] db.flush failed: {_dbf_err}")
                            try:
                                db.rollback()
                            except Exception:
                                pass
                    # 减仓成功后记录冷却
                    if ENABLE_REDUCE_COOLDOWN:
                        from backend.services.reentry_cooldown import record_partial_close
                        _pnl = result.get("pnl", 0) if isinstance(result, dict) else 0
                        _pos_side = pos.get("side", "long")
                        record_partial_close(account_id, sym, _pos_side, tier, _pnl)
                    # ── 整改项4: 减仓后更新策略记忆 ────────────────────
                    try:
                        from backend.services.position_memory_manager import update_partial_close_memory
                        _strategy_id = pos.get("strategy_id") or pos.get("ai_strategy_id")
                        _reduce_pnl = result.get("pnl", 0) if isinstance(result, dict) else 0
                        if _strategy_id:
                            update_partial_close_memory(
                                db=db, strategy_id=_strategy_id, symbol=sym,
                                partial_pnl=_reduce_pnl, reduce_ratio=reduce_ratio or 0.5,
                                tier=tier,
                            )
                    except Exception as e:
                        logger.warning(f"[FullAuto] 减仓记忆更新异常(非致命): {e}")
                    host.mark_master_decision_executed(_snap_entry, _dec_log_entry, db)

        elif action == "pyramid" and mode == "running" and pos:
            # ── 顺势加仓（金字塔）— 先经过趋势门控，再 evaluate_pyramid ──
            # v3 整改: rebound gate — 刚减仓未满冷却 → 抑制加仓（防 reduce→rebuild 死亡螺旋）
            try:
                from backend.services.reentry_cooldown import is_reduce_cooling_down as _is_rcd
                _rcd_flag, _rcd_reason = _is_rcd(
                    account_id, sym, pos.get("side", "long"), tier
                )
                if _rcd_flag:
                    host.append_event(session, "rebound_gate_blocked",
                        f"🚫 滚仓冷却拦截 {sym}[{pos_log_scope}] pyramid: {_rcd_reason}")
                    continue
            except Exception as _rcd_err:
                logger.warning(f"[FullAuto] rebound gate 检查异常(拦截) {sym}: {_rcd_err}")
                host.append_event(session, "rebound_gate_error",
                    f"滚仓冷却检查异常，拦截 pyramid {sym}: {_rcd_err}")
                continue
            try:
                from backend.services.position_memory_manager import position_manager, trend_pyramid_gate

                # 构建门控所需的 market_summary 字典
                _gate_ms = {}
                if isinstance(market_summary, dict):
                    _sym_mkt = market_summary.get(sym, {})
                    _gate_ms["orchestrator"] = {
                        "final_action": _sym_mkt.get("orchestrator", {}).get("final_action", "wait") if isinstance(_sym_mkt.get("orchestrator"), dict) else "wait",
                        "final_side": _sym_mkt.get("orchestrator", {}).get("final_side", "") if isinstance(_sym_mkt.get("orchestrator"), dict) else "",
                        "long_view_bias": _sym_mkt.get("orchestrator", {}).get("long_view_bias", "neutral") if isinstance(_sym_mkt.get("orchestrator"), dict) else "neutral",
                        "mid_view_bias": _sym_mkt.get("orchestrator", {}).get("mid_view_bias", "neutral") if isinstance(_sym_mkt.get("orchestrator"), dict) else "neutral",
                        "short_view_bias": _sym_mkt.get("orchestrator", {}).get("short_view_bias", "neutral") if isinstance(_sym_mkt.get("orchestrator"), dict) else "neutral",
                    }
                    try:
                        from backend.services.unified_data_pool import UnifiedDataPool
                        _snap = UnifiedDataPool().get_snapshot(max_age=60)
                        if _snap:
                            _gate_ms["indicators"] = _snap.indicators
                    except Exception:
                        pass

                _add_count = pos.get("add_count", 0) or 0
                _pos_margin = float(pos.get("margin", 0))
                _pos_upnl = float(pos.get("unrealized_pnl", 0))
                _pnl_pct = _pos_upnl / _pos_margin if _pos_margin > 0 else 0

                gate_ok, gate_reason = trend_pyramid_gate(
                    sym, pos.get("side", "long"), _add_count, _pnl_pct, _gate_ms, tier=tier
                )

                if not gate_ok:
                    host.append_event(session, "pyramid_gate_blocked",
                        f"🚫 滚仓门控拦截 {sym}[{pos_log_scope}]: {gate_reason}")
                else:
                    plan = position_manager.evaluate_pyramid(
                        db=db, account_id=account_id, symbol=sym,
                        side=pos.get("side", "long"),
                        ai_confidence=confidence / 100.0,
                        current_price=float(pos.get("mark_price", 0)),
                        existing_position=pos,
                        volatility_pct=float((market_summary or {}).get(sym, {}).get("volatility_value", 0.015) or 0.015),
                        tier=tier,
                        market_summary=_gate_ms,
                    )
                    if plan.action == "pyramid":
                        qty = plan.notional_usd / float(pos.get("mark_price", 1)) if pos.get("mark_price") else 0
                        if qty > 0:
                            result = paper_engine.place_order(
                                db, account_id, sym,
                                "buy" if pos.get("side") == "long" else "sell",
                                quantity=qty, leverage=float(pos.get("leverage", 10)),
                                tp_price=plan.take_profit_price, sl_price=plan.stop_loss_price,
                                strategy_id=pos.get("strategy_id"),
                                timeframe_tier=tier,
                                add_type="pyramid",
                            )
                            if result and result.get("status") == "filled":
                                session.total_trades = (session.total_trades or 0) + 1
                                host.append_event(session, "pyramid_executed",
                                    f"📈 顺势加仓 {sym}[{pos_log_scope}] +${plan.margin_usd:.0f} | {reasoning}")
                                _position_dirty = True
                    else:
                        host.append_event(session, "pyramid_skip",
                            f"📊 {sym}[{pos_log_scope}] 加仓条件不满足: {plan.reasoning}")
            except Exception as pyr_err:
                logger.warning(f"[FullAuto] pyramid执行异常 {sym}: {pyr_err}", exc_info=True)

        elif action == "dca" and mode == "running" and pos:
            # ── 逆势补仓 ──
            # 2026-04-27: 补仓前检查平仓冷却 — 刚平仓的同 tier 不得立即 DCA
            try:
                from backend.services.reentry_cooldown import reopen_blocked as _rb
                _rb_flag, _rb_why = _rb(account_id, sym, "dca", new_tier=tier)
                if _rb_flag:
                    host.append_event(session, "dca_blocked_by_close_cooldown",
                        f"🚫 平仓冷却拦截 DCA {sym}[{pos_log_scope}]: {_rb_why}")
                    continue
            except Exception as _rb_err:
                logger.warning(f"[FullAuto] DCA close-cooldown check error(拦截): {_rb_err}")
                host.append_event(session, "dca_cooldown_error",
                    f"DCA冷却检查异常，拦截 {sym}: {_rb_err}")
                continue
            try:
                from backend.services.reentry_cooldown import is_reduce_cooling_down as _is_rcd
                _rcd_flag, _rcd_reason = _is_rcd(
                    account_id, sym, pos.get("side", "long"), tier
                )
                if _rcd_flag:
                    host.append_event(session, "rebound_gate_blocked",
                        f"🚫 滚仓冷却拦截 {sym}[{pos_log_scope}] dca: {_rcd_reason}")
                    continue
            except Exception as _rcd_err:
                logger.warning(f"[FullAuto] rebound gate 检查异常(拦截) {sym}: {_rcd_err}")
                host.append_event(session, "rebound_gate_error",
                    f"滚仓冷却检查异常，拦截 dca {sym}: {_rcd_err}")
                continue
            try:
                orch_decision = None
                mkt_data = (market_summary or {}).get(sym, {}) if isinstance(market_summary, dict) else {}
                if isinstance(mkt_data, dict):
                    orch_decision = mkt_data.get("orchestrator")
                plan = position_manager.evaluate_dca(
                    db=db, account_id=account_id, symbol=sym,
                    side="buy" if pos.get("side") == "long" else "sell",
                    ai_confidence=confidence / 100.0,
                    current_price=float(pos.get("mark_price", 0)),
                    existing_position=pos,
                    volatility_pct=float(mkt_data.get("volatility_value", 0.015) or 0.015),
                    market_regime=str(mkt_data.get("market_cycle", "unknown") or "unknown"),
                    orchestrator_decision=orch_decision,
                    risk_score=float((analyst_reports or {}).get("risk", {}).get("risk_score", 50) if isinstance((analyst_reports or {}).get("risk"), dict) else 50),
                    tier=tier,
                )
                if plan.action == "dca":
                    qty = plan.notional_usd / float(pos.get("mark_price", 1)) if pos.get("mark_price") else 0
                    if qty > 0:
                        # DCA 杠杆限制：逆势补仓使用保守杠杆，防止死亡螺旋
                        _dca_orig_lev = float(pos.get("leverage", 10))
                        _dca_lev = min(5, max(2, _dca_orig_lev / 2))
                        result = paper_engine.place_order(
                            db, account_id, sym,
                            "buy" if pos.get("side") == "long" else "sell",
                            quantity=qty, leverage=_dca_lev,
                            tp_price=plan.take_profit_price, sl_price=plan.stop_loss_price,
                            strategy_id=pos.get("strategy_id"),
                            timeframe_tier=tier,
                            add_type="dca",
                        )
                        if result and result.get("status") == "filled":
                            session.total_trades = (session.total_trades or 0) + 1
                            host.append_event(session, "dca_executed",
                                f"📉 逆势补仓 {sym}[{pos_log_scope}] +${plan.margin_usd:.0f} | {reasoning}")
                            _position_dirty = True
                else:
                    host.append_event(session, "dca_skip",
                        f"📊 {sym}[{pos_log_scope}] 补仓条件不满足: {plan.reasoning}")
            except Exception as dca_err:
                logger.warning(f"[FullAuto] dca执行异常 {sym}: {dca_err}", exc_info=True)

        elif action in ("buy", "sell") and mode == "running":
            # ── Pace 开平对称：shadow 平仓时禁止新开仓 ──
            try:
                from backend.services.paper_pace_controller import paper_pace_controller
                if paper_pace_controller.blocks_new_opens_symmetric():
                    host.append_event(session, "pace_symmetric_block",
                        f"⛔ Pace shadow 对称禁开 {sym} {action}")
                    logger.info(f"[FullAuto] Pace 对称禁开 {sym} {action}")
                    continue
            except Exception:
                pass

            # ── 三层预算检查（2026-06-18）──
            # 该 nature 所属层的预算是否够开这仓
            try:
                from backend.services.budget_service import budget_service
                _layer = budget_service.tier_to_layer(_dec_nature_raw)
                _equity = float((balance_info or {}).get("equity", 0) or (balance_info or {}).get("total_equity", 0) or 0)
                _req_margin = float(dec.get("margin_usd", 0) or dec.get("_sizing_margin_usd", 0) or 0)
                _tm = host.session_trading_mode(session)
                _budget_acct = (
                    getattr(session, "paper_account_id", None)
                    or getattr(session, "account_id", None)
                )
                if _equity > 0 and _req_margin > 0:
                    _bf = budget_service.scale_factor_for_layer(
                        tier or _dec_nature_raw, _equity, _tm,
                        account_id=_budget_acct,
                    )
                    if _bf <= 0:
                        host.append_event(session, "layer_budget_block",
                            f"📊 {_layer}层预算已满，跳过 {sym} {action}")
                        continue
                    if _bf < 1.0:
                        dec["size_multiplier"] = float(dec.get("size_multiplier") or 1.0) * _bf
                    elif not budget_service.can_open(
                        tier or "mid", _req_margin, _equity, _tm,
                        account_id=_budget_acct,
                    ):
                        host.append_event(session, "layer_budget_block",
                            f"📊 {_layer}层预算不足，跳过 {sym} {action}")
                        continue
            except Exception:
                pass
            _train_allowed = getattr(host, "training_allowed_symbols", set())
            if _train_allowed and sym.upper() not in _train_allowed:
                host.append_event(session, "training_universe_block",
                    f"⛔ 训练期非目标币禁开 {sym} {action}")
                logger.info(f"[FullAuto] 训练期禁开非目标币 {sym} {action}")
                continue

            # ── 同 tier 同向持仓时的处理（AI 主驾改造）──
            # 2026-06-18: 原"自动转译 buy→pyramid/dca"会偷换 AI 的 action 语义
            # （AI 想开新仓，系统执行成加仓，AI 不知情）。现改为：
            # - 保留 AI 的 action（buy/sell）
            # - 检查该 symbol+side 已有开仓数，未达子仓上限(MAX_SUB_POSITIONS_PER_SYMBOL)
            #   则放行开新子仓；已达上限则改 hold（硬安全网 veto，非数值改写）
            # - AI 若真想加仓，应在 prompt 里输出 pyramid/dca（系统已支持这两个 action）
            _want_side = "long" if action == "buy" else "short"
            _dec_tier_for_match = (dec.get("timeframe_tier") or tier or "").strip().lower()
            if _dec_tier_for_match not in ("short", "mid", "long"):
                _dec_tier_for_match = "mid"

            # 统计该 symbol + 同方向 的已有开仓数（不限 tier）
            _same_side_open_count = sum(
                1 for _ep in (positions_list or [])
                if _ep.get("symbol") == sym
                and _ep.get("side") == _want_side
                and _ep.get("status") == "open"
            )

            # 找同 tier 同向仓位（用于日志/决策上下文，不再用于偷换 action）
            _same_dir_pos = None
            for _ep in (positions_list or []):
                if (_ep.get("symbol") == sym
                        and _ep.get("side") == _want_side
                        and _ep.get("status") == "open"):
                    _ep_tier = (_ep.get("timeframe_tier") or "mid").strip().lower()
                    if _ep_tier == _dec_tier_for_match:
                        _same_dir_pos = _ep
                        break

            try:
                from backend.config.settings import MAX_SUB_POSITIONS_PER_SYMBOL as _MAX_SUB
            except Exception:
                _MAX_SUB = 3

            if _same_side_open_count >= _MAX_SUB:
                # 硬安全网：已达子仓上限，改为 hold（非数值改写，是数量 veto）
                _orig_action = action
                action = "hold"
                logger.info(
                    f"[FullAuto] 子仓上限veto {sym}[{_dec_tier_for_match}] {_orig_action}→hold "
                    f"(已有{_same_side_open_count}/{_MAX_SUB}个同向仓位)"
                )
            elif _same_dir_pos:
                # 有同 tier 同向仓但未达上限：保留 AI 的 buy/sell，开新子仓
                logger.info(
                    f"[FullAuto] {sym}[{_dec_tier_for_match}] 保留AI action={action} "
                    f"(已有同tier同向仓, 现有{_same_side_open_count}/{_MAX_SUB}, 开新子仓; "
                    f"AI若想加仓应输出pyramid/dca)"
                )
                # 同 symbol 同 side 但不同 tier 已有仓位：放行去开新 tier 独立仓位
            # （原 else 分支的"不同 tier 已有仓位日志"已合并进上面的
            # _same_side_open_count 统计，此处不再需要单独日志）

            if action == "pyramid" and _same_dir_pos:
                # ── 顺势加仓（金字塔）— 先经过趋势门控，再 evaluate_pyramid ──
                # v3 整改: rebound gate — 刚减仓未满冷却 → 抑制加仓（防 reduce→rebuild 死亡螺旋）
                try:
                    from backend.services.reentry_cooldown import is_reduce_cooling_down as _is_rcd
                    _rcd_tier = (_same_dir_pos.get("timeframe_tier") or tier or "mid").strip().lower()
                    if _rcd_tier not in ("short", "mid", "long"):
                        _rcd_tier = "mid"
                    _rcd_flag, _rcd_reason = _is_rcd(
                        account_id, sym, _same_dir_pos.get("side", "long"), _rcd_tier
                    )
                    if _rcd_flag:
                        host.append_event(session, "rebound_gate_blocked",
                            f"🚫 滚仓冷却拦截 {sym}[{pos_log_scope}] pyramid(转译): {_rcd_reason}")
                        continue
                except Exception as _rcd_err:
                    logger.warning(f"[FullAuto] rebound gate 检查异常(拦截) {sym}: {_rcd_err}")
                    host.append_event(session, "rebound_gate_error",
                        f"滚仓冷却检查异常，拦截 pyramid(转译) {sym}: {_rcd_err}")
                    continue
                try:
                    from backend.services.position_memory_manager import position_manager, trend_pyramid_gate

                    # 构建门控所需的 market_summary 字典
                    _gate_ms = {}
                    if isinstance(market_summary, dict):
                        _sym_mkt = market_summary.get(sym, {})
                        _gate_ms["orchestrator"] = {
                            "final_action": _sym_mkt.get("orchestrator", {}).get("final_action", "wait") if isinstance(_sym_mkt.get("orchestrator"), dict) else "wait",
                            "final_side": _sym_mkt.get("orchestrator", {}).get("final_side", "") if isinstance(_sym_mkt.get("orchestrator"), dict) else "",
                            "long_view_bias": _sym_mkt.get("orchestrator", {}).get("long_view_bias", "neutral") if isinstance(_sym_mkt.get("orchestrator"), dict) else "neutral",
                            "mid_view_bias": _sym_mkt.get("orchestrator", {}).get("mid_view_bias", "neutral") if isinstance(_sym_mkt.get("orchestrator"), dict) else "neutral",
                            "short_view_bias": _sym_mkt.get("orchestrator", {}).get("short_view_bias", "neutral") if isinstance(_sym_mkt.get("orchestrator"), dict) else "neutral",
                        }
                        try:
                            from backend.services.unified_data_pool import UnifiedDataPool
                            _snap = UnifiedDataPool().get_snapshot(max_age=60)
                            if _snap:
                                _gate_ms["indicators"] = _snap.indicators
                        except Exception:
                            pass

                    _add_count = _same_dir_pos.get("add_count", 0) or 0
                    _pos_margin = float(_same_dir_pos.get("margin", 0))
                    _pos_upnl = float(_same_dir_pos.get("unrealized_pnl", 0))
                    _pnl_pct = _pos_upnl / _pos_margin if _pos_margin > 0 else 0

                    gate_ok, gate_reason = trend_pyramid_gate(
                        sym, _same_dir_pos.get("side", "long"), _add_count, _pnl_pct, _gate_ms, tier=tier
                    )

                    if not gate_ok:
                        host.append_event(session, "pyramid_gate_blocked",
                            f"🚫 滚仓门控拦截 {sym}[{pos_log_scope}]: {gate_reason}")
                    else:
                        plan = position_manager.evaluate_pyramid(
                            db=db, account_id=account_id, symbol=sym,
                            side=_same_dir_pos.get("side", "long"),
                            ai_confidence=confidence / 100.0,
                            current_price=float(_same_dir_pos.get("mark_price", 0)),
                            existing_position=_same_dir_pos,
                            volatility_pct=float((market_summary or {}).get(sym, {}).get("volatility_value", 0.015) or 0.015),
                            tier=tier,
                            market_summary=_gate_ms,
                        )
                        if plan.action == "pyramid":
                            qty = plan.notional_usd / float(_same_dir_pos.get("mark_price", 1)) if _same_dir_pos.get("mark_price") else 0
                            if qty > 0:
                                result = paper_engine.place_order(
                                    db, account_id, sym,
                                    "buy" if _same_dir_pos.get("side") == "long" else "sell",
                                    quantity=qty, leverage=float(_same_dir_pos.get("leverage", 10)),
                                    tp_price=plan.take_profit_price, sl_price=plan.stop_loss_price,
                                    strategy_id=_same_dir_pos.get("strategy_id"),
                                    timeframe_tier=tier,
                                    add_type="pyramid",
                                )
                                if result and result.get("status") == "filled":
                                    session.total_trades = (session.total_trades or 0) + 1
                                    host.append_event(session, "pyramid_executed",
                                        f"📈 顺势加仓 {sym}[{pos_log_scope}] +${plan.margin_usd:.0f} | {reasoning}")
                                    _position_dirty = True
                                    host.mark_master_decision_executed(_snap_entry, _dec_log_entry, db)
                        else:
                            host.append_event(session, "pyramid_skip",
                                f"📊 {sym}[{pos_log_scope}] 加仓条件不满足: {plan.reasoning}")
                except Exception as pyr_err:
                    logger.warning(f"[FullAuto] pyramid执行异常 {sym}: {pyr_err}", exc_info=True)

            elif action == "dca" and _same_dir_pos:
                # ── 逆势补仓（转译）──
                # 2026-04-27: 补仓前检查平仓冷却 — 刚平仓的同 tier 不得立即 DCA
                try:
                    from backend.services.reentry_cooldown import reopen_blocked as _rb
                    _dca_tier = (_same_dir_pos.get("timeframe_tier") or tier or "mid").strip().lower()
                    if _dca_tier not in ("short", "mid", "long"):
                        _dca_tier = "mid"
                    _rb_flag, _rb_why = _rb(account_id, sym, "dca", new_tier=_dca_tier)
                    if _rb_flag:
                        host.append_event(session, "dca_blocked_by_close_cooldown",
                            f"🚫 平仓冷却拦截 DCA {sym}[{pos_log_scope}](转译): {_rb_why}")
                        continue
                except Exception as _rb_err:
                    logger.warning(f"[FullAuto] DCA close-cooldown check error(拦截): {_rb_err}")
                    host.append_event(session, "dca_cooldown_error",
                        f"DCA冷却检查异常，拦截(转译) {sym}: {_rb_err}")
                    continue
                # v3 整改: rebound gate — 刚减仓未满冷却 → 抑制加仓（防 reduce→rebuild 死亡螺旋）
                try:
                    from backend.services.reentry_cooldown import is_reduce_cooling_down as _is_rcd
                    _rcd_tier = (_same_dir_pos.get("timeframe_tier") or tier or "mid").strip().lower()
                    if _rcd_tier not in ("short", "mid", "long"):
                        _rcd_tier = "mid"
                    _rcd_flag, _rcd_reason = _is_rcd(
                        account_id, sym, _same_dir_pos.get("side", "long"), _rcd_tier
                    )
                    if _rcd_flag:
                        host.append_event(session, "rebound_gate_blocked",
                            f"🚫 滚仓冷却拦截 {sym}[{pos_log_scope}] dca(转译): {_rcd_reason}")
                        continue
                except Exception as _rcd_err:
                    logger.warning(f"[FullAuto] rebound gate 检查异常(拦截) {sym}: {_rcd_err}")
                    host.append_event(session, "rebound_gate_error",
                        f"滚仓冷却检查异常，拦截 dca(转译) {sym}: {_rcd_err}")
                    continue
                try:
                    from backend.services.position_memory_manager import position_manager
                    orch_decision = None
                    mkt_data = (market_summary or {}).get(sym, {}) if isinstance(market_summary, dict) else {}
                    if isinstance(mkt_data, dict):
                        orch_decision = mkt_data.get("orchestrator")
                    plan = position_manager.evaluate_dca(
                        db=db, account_id=account_id, symbol=sym,
                        side="buy" if _same_dir_pos.get("side") == "long" else "sell",
                        ai_confidence=confidence / 100.0,
                        current_price=float(_same_dir_pos.get("mark_price", 0)),
                        existing_position=_same_dir_pos,
                        volatility_pct=float(mkt_data.get("volatility_value", 0.015) or 0.015),
                        market_regime=str(mkt_data.get("market_cycle", "unknown") or "unknown"),
                        orchestrator_decision=orch_decision,
                        risk_score=float((analyst_reports or {}).get("risk", {}).get("risk_score", 50) if isinstance((analyst_reports or {}).get("risk"), dict) else 50),
                        tier=tier,
                    )
                    if plan.action == "dca":
                        qty = plan.notional_usd / float(_same_dir_pos.get("mark_price", 1)) if _same_dir_pos.get("mark_price") else 0
                        if qty > 0:
                            # DCA 杠杆限制：逆势补仓使用保守杠杆
                            _trans_dca_lev = min(5, max(2, float(_same_dir_pos.get("leverage", 10)) / 2))
                            result = paper_engine.place_order(
                                db, account_id, sym,
                                "buy" if _same_dir_pos.get("side") == "long" else "sell",
                                quantity=qty, leverage=_trans_dca_lev,
                                tp_price=plan.take_profit_price, sl_price=plan.stop_loss_price,
                                strategy_id=_same_dir_pos.get("strategy_id"),
                                timeframe_tier=tier,
                                add_type="dca",
                            )
                            if result and result.get("status") == "filled":
                                session.total_trades = (session.total_trades or 0) + 1
                                host.append_event(session, "dca_executed",
                                    f"📉 逆势补仓 {sym}[{pos_log_scope}] +${plan.margin_usd:.0f} | {reasoning}")
                                _position_dirty = True
                                host.mark_master_decision_executed(_snap_entry, _dec_log_entry, db)
                    else:
                        host.append_event(session, "dca_skip",
                            f"📊 {sym}[{pos_log_scope}] 补仓条件不满足: {plan.reasoning}")
                except Exception as dca_err:
                    logger.warning(f"[FullAuto] dca执行异常 {sym}: {dca_err}", exc_info=True)

            if not _same_dir_pos:
                # ── 无同向持仓，正常开新仓 ──
                # ── 同向再开仓冷却：刚全平后 N 分钟内禁止同方向再开（防刷手续费）──
                try:
                    from backend.services.reentry_cooldown import reopen_blocked
                    # v5: tier-isolated 冷却，读取决策的 tier 保证只阻断同 tier 同向再开
                    _dec_tier_for_cd = (dec.get("timeframe_tier") or tier or "").strip().lower()
                    if _dec_tier_for_cd not in ("short", "mid", "long"):
                        _dec_tier_for_cd = "mid"
                    _blocked, _why = reopen_blocked(
                        account_id, sym, action, new_tier=_dec_tier_for_cd
                    )
                    if _blocked:
                        _orch_blk, _orch_why = host.orchestrator_blocks_open(
                            sym, action, market_summary, _dec_tier_for_cd,
                            confidence=confidence, trading_mode=mode,
                        )
                        if _orch_blk:
                            host.append_event(session, "orchestrator_gate_block",
                                f"⛔ 编排器拦截 {sym} {action}: wait | "
                                f"冷却排队已取消 — {_orch_why[:60]}")
                            host.clear_deferred_signal(
                                account_id, sym, action, _dec_tier_for_cd)
                            logger.info(
                                f"[FullAuto] 编排器wait，不排队延迟信号 {sym} {action}[{_dec_tier_for_cd}]"
                            )
                            continue
                        # 信号延迟排队，而非直接丢弃：冷却期满后自动重试
                        import re as _re_mod
                        _remain_match = _re_mod.search(r'(\d+)s', _why)
                        _remain_sec = int(_remain_match.group(1)) if _remain_match else 300
                        _defer_key = host.deferred_signal_key(
                            account_id, sym, action, _dec_tier_for_cd,
                        )
                        _prev = host.deferred_signals.get(_defer_key, {})
                        host.deferred_signals[_defer_key] = {
                            "account_id": account_id,
                            "symbol": sym,
                            "action": action,
                            "confidence": dec.get("confidence", 50),
                            "reasoning": dec.get("reasoning", ""),
                            "trade_nature": dec.get("trade_nature", ""),
                            "timeframe_tier": _dec_tier_for_cd,
                            "strategy_id": dec.get("strategy_id"),
                            "deferred_at": time.time(),
                            "cooldown_expires": time.time() + _remain_sec,
                            "defer_count": int(_prev.get("defer_count", 0) or 0),
                            "session_id": session.get("id") if isinstance(session, dict)
                                          else getattr(session, "id", None),
                        }
                        host.append_event(session, "reentry_deferred",
                            f"⏳ 信号已排队 {sym} {action}: {_why} | 冷却后自动重试")
                        logger.info(
                            f"[FullAuto] 信号排队: {sym} {action} — {_why}, "
                            f"预计{_remain_sec}s后重试"
                        )
                        # 深挖第 3 项 (2026-05-08)：guard 拦截事件持久化
                        try:
                            from backend.services.unified_risk_gate import record_guard_block
                            record_guard_block(
                                db, account_id=account_id,
                                guard_name="reentry_cooldown",
                                symbol=sym, side=action,
                                reason=_why,
                                extra={"point": "open_new", "tier": _dec_tier_for_cd,
                                       "remain_sec": _remain_sec},
                            )
                        except Exception:
                            pass
                        continue
                except Exception as _re_err:
                    logger.warning(f"[FullAuto] reentry_cooldown 检查异常: {_re_err}", exc_info=True)

                # ── 硬风控：risk score 拦截 ──
                if risk_block_new_positions:
                    host.append_event(session, "risk_gate_block",
                        f"⛔ 拦截 {sym} {action}: 风险过高(score>80)")
                    continue

                # ── 多周期并行：per-tier 保证金上限检查 ──
                _tier_cap = _tier_budget_caps.get(tier, 0)
                _tier_used = _tier_margin_used.get(tier, 0)
                if _tier_cap > 0 and _tier_used >= _tier_cap:
                    host.append_event(session, "tier_budget_block",
                        f"⛔ {sym} {action}: {tier}层保证金已满 "
                        f"({_tier_used:.1f}/{_tier_cap:.1f})")
                    logger.info(
                        f"[FullAuto] tier预算拦截 {sym}[{tier}]: "
                        f"used={_tier_used:.1f} >= cap={_tier_cap:.1f}")
                    continue

                # ── P0-E 分层熔断：周期级日亏预算（只冻本 tier，绝不跨周期）──
                try:
                    from backend.services.tier_circuit_breaker import (
                        is_tier_open_blocked as _tier_cb_blocked,
                    )
                    _tier_blk, _tier_why = _tier_cb_blocked(account_id, tier)
                    if _tier_blk:
                        host.append_event(session, "tier_circuit_block",
                            f"⛔ 周期熔断拦截 {sym}[{tier}] {action}: {_tier_why[:80]}")
                        logger.info(
                            f"[FullAuto] 周期熔断拦截 {sym}[{tier}]: {_tier_why[:80]}")
                        continue
                except Exception as _tier_cb_err:
                    logger.warning(
                        f"[FullAuto] tier_circuit_breaker 检查异常: {_tier_cb_err}")

                # ── 编排器硬门控 ──
                try:
                    _orch_blk, _orch_why = host.orchestrator_blocks_open(
                        sym, action, market_summary, tier, confidence=confidence,
                        trading_mode=mode,
                    )
                    if _orch_blk:
                        _orch = ((market_summary or {}).get(sym, {}) or {}).get("orchestrator", {})
                        _orch_action = (
                            _orch.get("action") if isinstance(_orch, dict) else "wait"
                        )
                        host.append_event(session, "orchestrator_gate_block",
                            f"⛔ 编排器拦截 {sym} {action}: {_orch_action} | "
                            f"{str(_orch.get('reasoning', '') if isinstance(_orch, dict) else _orch_why)[:80]}")
                        host.clear_deferred_signal(account_id, sym, action, tier)
                        logger.info(f"[FullAuto] 编排器硬门控拦截 {sym} {action}: "
                                    f"orch={_orch_action}")
                        continue
                except Exception:
                    pass

                # ── DCP tier 方向约束（替代 neutral 无条件放行）──
                try:
                    from backend.services.decision_core.direction_coherence import (
                        evaluate_direction_coherence,
                    )
                    _mkt_dir = (market_summary or {}).get(sym, {}) if isinstance(market_summary, dict) else {}
                    _orch_dir = _mkt_dir.get("orchestrator", {}) if isinstance(_mkt_dir, dict) else {}
                    _dcp_tier = evaluate_direction_coherence(
                        action=action,
                        confidence=confidence,
                        tier=tier,
                        trade_nature=trade_nature,
                        orchestrator=_orch_dir if isinstance(_orch_dir, dict) else {},
                        fan_branch=dec.get("_fan_branch") or "",
                        symbol=sym,
                        trading_mode=mode,
                    )
                    if not _dcp_tier.allowed:
                        host.append_event(session, "direction_gate_block",
                            f"⛔ DCP tier拦截 {sym}[{tier}] {action}: {_dcp_tier.reason[:80]}")
                        logger.info(
                            f"[FullAuto] DCP tier拦截 {sym} {action} tier={tier}: "
                            f"{_dcp_tier.rule}"
                        )
                        continue
                except Exception:
                    pass

                # ── [Phase7] 趋势锁定：trend_follow/position 性质时禁止反向开仓 ──
                try:
                    if trade_nature in ("trend_follow", "position"):
                        # 检查同 symbol 是否已有同方向趋势仓
                        _same_dir_trend = False
                        for _ep in (positions_list or []):
                            if _ep.get("symbol") == sym:
                                _ep_side = _ep.get("side", "")
                                _want_side = "long" if action == "buy" else "short"
                                if _ep_side != _want_side:
                                    host.append_event(session, "trend_lock_block",
                                        f"🔒 趋势锁定拦截 {sym} {action}: "
                                        f"已有{_ep_side}趋势仓，禁止反向开仓")
                                    _same_dir_trend = True
                                    break
                        if _same_dir_trend:
                            continue
                except Exception:
                    pass

                # ── [Phase7] 手续费成本门槛：预期利润不足手续费 3 倍时跳过 ──
                try:
                    _ref_price_fee = 0.0
                    try:
                        from backend.services.market_data import get_last_price
                        _ref_price_fee = get_last_price(sym) or 0
                    except Exception:
                        pass
                    if _ref_price_fee > 0 and total_equity > 0:
                        _est_notional = total_equity * 0.10 * 4
                        _est_fee = _est_notional * 0.0006
                        _est_profit = _est_notional * 0.01 * (confidence / 100.0)
                        if _est_profit < _est_fee * 3 and _est_fee > 0:
                            _msg = (
                                f"💸 手续费门槛拦截 {sym} {action}: "
                                f"预期利润${_est_profit:.2f}<手续费${_est_fee:.2f}×3"
                            )
                            host.append_event(session, "fee_threshold_block", _msg)
                            try:
                                from backend.services.block_report_aggregator import record_block
                                record_block("fee_threshold", _msg)
                            except Exception:
                                pass
                            continue
                except Exception:
                    pass

                # P2-2: 历史表现门控 — 阻止在已知亏损方向开仓
                try:
                    from backend.config.settings import (
                        PERFORMANCE_GATE_ENABLED, PERFORMANCE_GATE_MIN_SAMPLES,
                        PERFORMANCE_GATE_MIN_WR,
                    )
                    if PERFORMANCE_GATE_ENABLED and action in ("buy", "sell"):
                        _pg_dir = "long" if action == "buy" else "short"
                        # warmup/growth 期（该 symbol+方向样本未成熟）不封锁，
                        # 让模拟盘继续累积数据；成熟后再按真实胜率拦截。live 恒 mature。
                        _pg_stage = "mature"
                        try:
                            from backend.services.maturity_controller import resolve_relief
                            _pg_stage = str(resolve_relief(
                                symbol=sym, side=action, mode="paper",
                            ).get("stage", "mature"))
                        except Exception:
                            pass
                        _pg_wr, _pg_n = host.get_symbol_direction_wr(sym, _pg_dir)
                        if (
                            _pg_stage == "mature"
                            and _pg_n >= PERFORMANCE_GATE_MIN_SAMPLES
                            and _pg_wr < PERFORMANCE_GATE_MIN_WR
                        ):
                            _msg = (
                                f"🚫 历史表现拦截 {sym} {action}: "
                                f"{_pg_dir}胜率{_pg_wr*100:.0f}%({_pg_n}笔)<{PERFORMANCE_GATE_MIN_WR*100:.0f}%"
                            )
                            host.append_event(session, "performance_gate_block", _msg)
                            logger.info(f"[FullAuto] P2-2: {_msg}")
                            continue
                except Exception as _pg_err:
                    logger.debug(f"[FullAuto] 历史表现门控异常(放行): {_pg_err}")

                # ── 统一风控（深挖第 3 轮 2026-05-08：UnifiedRiskGate）──
                # 同时跑两层规则，结果统一格式 + 自动落盘 risk_control_events
                try:
                    from backend.services.unified_risk_gate import unified_check
                    _pre_lev = 4
                    _notional_est = total_equity * 0.10
                    _margin_est = _notional_est / _pre_lev
                    _existing = [
                        {
                            "symbol": p.get("symbol", ""),
                            "side": p.get("side", ""),
                            "margin": float(p.get("margin", 0)),
                            "notional": float(p.get("size", 0)) * float(p.get("mark_price", 0)),
                            "size": float(p.get("size", 0)),
                            "leverage": float(p.get("leverage", 10)),
                        }
                        for p in (positions_list or [])
                    ]
                    _avail = (balance_info or {}).get("available_balance", 0)
                    _frozen = (balance_info or {}).get("frozen_margin", 0)
                    _margin_pct = (_frozen / total_equity * 100.0) if total_equity > 0 else 0.0
                    _ures = unified_check(
                        db=db, account_id=account_id,
                        symbol=sym, side=action,
                        notional=_notional_est, margin=_margin_est, leverage=_pre_lev,
                        total_equity=total_equity, available_balance=_avail,
                        frozen_margin=_frozen, margin_usage_percent=_margin_pct,
                        realized_pnl_today=host.get_today_realized_pnl(db, account_id),
                        existing_positions=_existing,
                        op_source="full_auto",
                    )
                    if not _ures.passed:
                        _msg = (
                            f"⛔ 风控拦截 {sym} {action}: {_ures.reason_text} "
                            f"[layer={_ures.blocked_layer} rule={_ures.blocked_rule}]"
                        )
                        host.append_event(session, f"{_ures.blocked_layer}_block", _msg)
                        logger.info(f"[FullAuto] 风控拦截: {_ures.reason_text}")
                        try:
                            from backend.services.block_report_aggregator import record_block
                            record_block(
                                f"unified:{_ures.blocked_layer}:{_ures.blocked_rule}",
                                _msg,
                            )
                        except Exception:
                            pass
                        continue
                    if _ures.warnings:
                        for _w in _ures.warnings:
                            logger.info(f"[FullAuto] 风控告警(不阻塞) {sym}: {_w['rule']} | {_w['message']}")
                except Exception as _rg_err:
                    logger.warning(f"[FullAuto] 统一风控异常(拦截): {_rg_err}")
                    host.append_event(session, "risk_gate_error",
                        f"统一风控异常，拦截 {sym} {action}: {_rg_err}")
                    continue

                # v3 整改: 删除 exposure_gate_block 段 —
                #   该检查与 deterministic_risk_gate Rule 2 (max_side_margin_pct) 重复，
                #   且只看实时持仓不含新单估算，可能被"先下后拦"绕过。
                #   统一由 DRG Rule 2 在 order.margin 上加新单后再判，行为更一致。

                # ── 从 market_summary 提取真实市场参数 ──
                mkt = market_summary.get(sym, {}) if isinstance(market_summary, dict) else {}
                orch = mkt.get("orchestrator", {}) if isinstance(mkt, dict) else {}
                if not isinstance(orch, dict):
                    orch = {}

                # 杠杆：AI 策略输出 > 编排器 > 动态计算器（风控 cap 仍生效）
                dyn_leverage, _lev_source = host.resolve_decision_leverage(
                    dec, sym, tier, mkt, db, account_id,
                    trade_nature=trade_nature, market_summary=market_summary,
                )
                logger.info(
                    f"[FullAuto] {sym}[{tier}] 杠杆={dyn_leverage}x (来源={_lev_source})"
                )

                # 真实波动率和市场周期（0 = 数据缺失，用保守默认值但标记）
                vol_value = 0.015
                _vol_from_data = False
                if isinstance(mkt, dict):
                    raw_vol = mkt.get("volatility_value", 0) or 0
                    if raw_vol >= 0.001:
                        vol_value = raw_vol
                        _vol_from_data = True
                    elif not mkt.get("data_reliable", True):
                        logger.debug(f"[FullAuto] {sym} 波动率数据缺失，使用默认1.5%")

                market_regime = "unknown"
                if isinstance(mkt, dict):
                    market_regime = mkt.get("market_cycle", "unknown") or "unknown"

                # TP/SL：根据 tier + 币种实际波动率 自适应计算
                sl_price = 0.0
                tp_price = 0.0
                tp_sl_source = "fixed_fallback"
                try:
                    from backend.services.market_data import get_last_price
                    _ref_price = get_last_price(sym) or 0
                    _atr_pct = vol_value  # 使用已获取的 vol_value（1h ATR 口径）
                    # P2 D10: 读取 1d ATR 作为 long tier 的真实尺度
                    _atr_1d_pct = 0.0
                    try:
                        _mkt_for_1d = mkt if isinstance(mkt, dict) else {}
                        _atr_1d_pct = float(
                            _mkt_for_1d.get("atr_1d_pct")
                            or _mkt_for_1d.get("atr_1d_value")
                            or 0.0
                        )
                    except Exception:
                        _atr_1d_pct = 0.0
                    if _ref_price > 0:
                        tp_price, sl_price, tp_sl_source = FullAutoTradingService._compute_initial_tp_sl_prices(
                            tier=tier,
                            action=action,
                            ref_price=_ref_price,
                            atr_pct=_atr_pct,
                            sym=sym,
                            atr_1d_pct=_atr_1d_pct,
                            dec=dec,
                        )
                        logger.info(
                            f"[TP/SL] {sym} tier={tier} source={tp_sl_source}: "
                            f"ref=${_ref_price:.4f} atr_1h={_atr_pct:.2%} "
                            f"atr_1d={_atr_1d_pct:.2%} "
                            f"tp=${tp_price:.4f} sl=${sl_price:.4f}"
                        )
                except Exception as e:
                    logger.warning(f"[TP/SL] {sym} 计算失败: {e}")

                # ── 动态置信度门槛（修改二）──
                # 原始 max(60,85) 导致 AI conf=50 校准后 40 永远无法入场
                # 调整为 max(45,70)：允许中等置信度（50校准后≥45）以小仓位入场
                entry_threshold = 50
                if isinstance(mkt, dict):
                    adapted_thresh = mkt.get("adapted_entry_threshold", 0)
                    if adapted_thresh and adapted_thresh > 0:
                        entry_threshold = int(adapted_thresh * 100)
                entry_threshold = max(45, min(70, entry_threshold))

                # [P3-修复] 原 hasattr(self,...) 在模块级函数中抛 NameError 直接中断整轮
                # master 执行（未包 try）。host.master_strat_cache 是 dataclass 字段恒存在，
                # 只需确保非 None。
                if not isinstance(host.master_strat_cache, dict):
                    host.master_strat_cache = {}
                # [tier-fix] cache_key 必须按 tier 隔离，否则同 symbol 多 tier 共用同一缓存
                # 会让后续 tier 的决策拿到先前 tier 的 strat（历史 bug：全部挤进 long）
                cache_key = f"{','.join(active_ids)}_{sym}_{tier}"

                # 只缓存 strategy_id；ORM 对象在 rollback/close 后会 detach
                cached_sid = host.master_strat_cache.get(cache_key)
                strat = None
                if cached_sid:
                    strat = host.load_strategy_by_id(
                        db, cached_sid,
                        active_ids=active_ids,
                        symbol=sym,
                        status=("active",),
                    )
                if strat is None:
                    # [fix] 防御性 rollback：循环中前面的 paper_engine 操作（place_order/
                    # close_position 内部有 db.flush/commit）可能失败导致 session 污染
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    host.clear_master_strat_cache()
                    _all_strats = db.query(_AIStrategy).filter(
                        _AIStrategy.strategy_id.in_(active_ids),
                        _AIStrategy.primary_symbol == sym,
                        _AIStrategy.status == "active",
                    ).all()
                    if _all_strats:
                        _tier_matching = [s for s in _all_strats
                            if (getattr(s, "timeframe_tier", None) or "").strip().lower() == tier]
                        if _tier_matching:
                            _nature_matching = [s for s in _tier_matching
                                if (s.genome or {}).get("trade_nature", "") == trade_nature]
                            strat = _nature_matching[0] if _nature_matching else max(_tier_matching, key=lambda s: s.id)
                        else:
                            # 安全网：CLOSE/REDUCE/HOLD 允许回退到任意可用策略（持仓管理）
                            _action_lower = (action or "").lower()
                            if _action_lower in ("close", "reduce", "hold", "adjust_sl", "adjust_tp"):
                                strat = max(_all_strats, key=lambda s: s.id)
                                logger.info(
                                    f"[FullAuto] {sym} tier={tier} 无对应策略，"
                                    f"持仓管理动作({_action_lower})回退到策略id={strat.strategy_id[:8]}"
                                    f"(tier={getattr(strat,'timeframe_tier','?')})"
                                )
                            else:
                                logger.warning(
                                    f"[FullAuto] {sym} tier={tier} 无对应策略，跳过该档决策"
                                    f"（候选策略 tiers={[getattr(s,'timeframe_tier',None) for s in _all_strats]}）"
                                )
                                strat = None
                        if strat is not None:
                            host.master_strat_cache[cache_key] = strat.strategy_id
                    else:
                        strat = None

                if strat:
                    try:
                        from backend.services.market_data import get_last_price
                        price = get_last_price(sym) or 0
                    except Exception:
                        price = 0

                    # P1-3: 做多不对称阈值 — 混合模式降低惩罚 (+15→+8)
                    _effective_entry_threshold = entry_threshold
                    if action == "buy":
                        _long_wr = host.get_direction_win_rate("long")
                        if _long_wr is not None and _long_wr < 0.40:
                            # 混合模式：做多胜率低于40%，阈值提高8个点（原15）
                            _effective_entry_threshold = min(90, entry_threshold + 8)

                    # 成熟度松紧：warmup 期放宽执行层置信门，避免与 V5 门重复收紧
                    # （live 模式 resolve_relief 强制返回 0，保持严格）
                    _mat_stage = "mature"
                    try:
                        from backend.services.maturity_controller import resolve_relief
                        _mat = resolve_relief(
                            symbol=sym, side=action,
                            nature=trade_nature, tier=tier, mode="paper",
                        )
                        _mat_relief = float(_mat.get("relief", 0) or 0)
                        _mat_stage = str(_mat.get("stage", "mature"))
                        if _mat_relief:
                            _effective_entry_threshold = max(
                                40, _effective_entry_threshold - _mat_relief,
                            )
                    except Exception:
                        pass

                    if price > 0 and confidence >= _effective_entry_threshold:
                        # 多周期预算感知：从 dec 中提取 tier 预算信息
                        _tier_budget_pct = float(dec.get("_tier_max_margin_pct", 0) or 0)
                        # 统一仓位规划：AI建议 → SizingAgent 风险预算约束 → 执行层保真
                        from backend.services.position_sizing_agent import (
                            PositionSizingInput,
                            position_sizing_agent,
                        )
                        _available_for_sizing = float(
                            (balance_info or {}).get("available_balance", 0)
                            or (balance_info or {}).get("available", 0)
                            or total_equity
                            or 0
                        )
                        _lev_cap = dec.get("leverage_cap")
                        try:
                            _lev_cap = int(_lev_cap) if _lev_cap is not None else None
                        except (TypeError, ValueError):
                            _lev_cap = None
                        _sizing_plan = position_sizing_agent.build_plan(
                            PositionSizingInput(
                                symbol=sym,
                                side=action,
                                price=float(price),
                                confidence=float(confidence),
                                total_equity=float(total_equity or 0.0),
                                available_balance=_available_for_sizing,
                                requested_leverage=float(dyn_leverage or 0),
                                requested_position_pct=host.extract_ai_position_pct(dec),
                                stop_loss_price=sl_price,
                                take_profit_price=tp_price,
                                volatility_pct=vol_value,
                                tier=tier,
                                trade_nature=trade_nature,
                                market_regime=market_regime if isinstance(market_regime, str) else "ranging",
                                risk_level="high" if mode == "defensive" else "medium",
                                tier_position_cap_pct=_tier_budget_pct,
                                size_multiplier=float(dec.get("size_multiplier") or 1.0),
                                leverage_cap=_lev_cap,
                                alignment_scale=host.resolve_alignment_scale(sym),
                            )
                        )
                        _final_pct = _sizing_plan.position_pct
                        _tdi_meta = {
                            "position_source": _sizing_plan.source,
                            "sizing_reasons": _sizing_plan.reasons,
                            "max_loss_usd": _sizing_plan.max_loss_usd,
                        }
                        decision_data = {
                            "action": action,
                            "side": action,
                            "price": price,
                            "leverage": _sizing_plan.leverage,
                            "position_pct": _final_pct,
                            "_leverage_source": _lev_source,
                            "confidence_pct": confidence,
                            "stop_loss_price": sl_price,
                            "take_profit_price": tp_price,
                            "market_regime": market_regime,
                            "volatility_pct": vol_value,
                            "timeframe_tier": tier,
                            "trade_nature": trade_nature,
                            "_tier_budget": dec.get("_tier_budget", 0),
                            "_tier_per_decision_budget": dec.get("_tier_per_decision_budget", 0),
                            "_tdi_meta": _tdi_meta,
                        }
                        decision_data.update(_sizing_plan.to_decision_fields())
                        host.log_pipeline_audit(sym, decision_data, action)
                        _trade_ok = host.execute_paper_trade(db, session, strat, decision_data)
                        if _trade_ok:
                            try:
                                from backend.services.short_tier_entry_gate import record_short_tier_open
                                record_short_tier_open(account_id, sym, action)
                            except Exception:
                                pass
                            _position_dirty = True
                            # 多周期并行：更新 tier 保证金追踪
                            _est_margin = (
                                float(decision_data.get("_tier_per_decision_budget", 0))
                                or total_equity * 0.05)
                            _tier_margin_used[tier] = _tier_margin_used.get(tier, 0) + _est_margin
                            host.mark_master_decision_executed(_snap_entry, _dec_log_entry, db)
                    elif price > 0 and confidence < _effective_entry_threshold:
                        host.append_event(session, "confidence_gate",
                            f"📉 {sym} {action} 置信度{confidence}%<有效门槛"
                            f"{_effective_entry_threshold:.0f}%(成熟度={_mat_stage})，不开仓")

        elif action in ("buy", "sell") and mode == "defensive":
            # Fix 23: defensive 模式不再无差别冻结所有 symbol
            # 原逻辑：defensive 下所有 buy/sell 被拦截 → 一个币亏损全局停摆
            # 新逻辑：
            #   - 有亏损持仓的 symbol → 冻结（不加仓不开反向）
            #   - 有盈利持仓的 symbol → 允许但 defensive 门槛已提高
            #   - 无持仓的新 symbol → 允许开仓（defensive 门槛 + 额外 +5%）
            if not host.paper_loss_locks_disabled(session):
                _is_losing_sym = False
                if pos:
                    try:
                        if float(pos.get("unrealized_pnl") or 0) < 0:
                            _is_losing_sym = True
                    except Exception:
                        pass
                if _is_losing_sym:
                    # 亏损持仓 → 冻结
                    host.append_event(session, "defensive_block",
                        f"🛡️ 防守模式: {sym} 持仓亏损中，冻结该 symbol")
                else:
                    # 盈利持仓或无持仓 → 允许，但提高门槛
                    _def_extra = 5
                    if confidence < (entry_threshold + _def_extra):
                        host.append_event(session, "defensive_gate",
                            f"🛡️ 防守模式: {sym} {action} 置信度{confidence}%<{entry_threshold + _def_extra}%(defensive额外+{_def_extra}%)")
                    else:
                        logger.info(f"[FullAuto] 防守模式允许 {sym} {action} (conf={confidence}%≥{entry_threshold + _def_extra}%)")

        # ── 整改项6: 决策完成后记录时间 ────────────────────
        if pos:
            _rec_pos_id = pos.get("id") or pos.get("position_id")
            if _rec_pos_id:
                host.record_position_decision(_rec_pos_id)

    # ── 批量提交 analytics：DecisionSnapshot + AIDecisionLog ──
    try:
        if _pending_snapshots:
            _analytics_db.add_all(_pending_snapshots)
        # AIDecisionLog 已在循环内逐条 commit，此处仅补交 DecisionSnapshot
    except Exception as _batch_err:
        logger.warning(f"[FullAuto] analytics 批量 add 失败: {_batch_err}")

    # ── 提交并关闭 analytics 会话（DecisionSnapshot / AIDecisionLog）──
    try:
        _analytics_db.commit()
    except Exception as _ana_commit_err:
        logger.warning(f"[FullAuto] analytics_db.commit() 失败: {_ana_commit_err}")
        try:
            _analytics_db.rollback()
        except Exception:
            pass
    finally:
        try:
            _analytics_db.close()
        except Exception:
            pass
