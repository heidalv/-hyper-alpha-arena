"""Per-symbol / 全局风控 — 从 monolith 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

@dataclass
class PerSymbolRiskResult:
    """per-symbol 风控检查结果"""
    global_freeze: bool = False
    global_reason: str = ""
    frozen_symbols: List[str] = field(default_factory=list)
    symbol_reasons: Dict[str, str] = field(default_factory=dict)
    # P0-E: symbol → 被冻结的 tier 列表（空列表=未记录=冻结全部 tier）
    frozen_symbol_tiers: Dict[str, List[str]] = field(default_factory=dict)


@dataclass
class SymbolRiskHost:
    symbol_daily_pnl: Dict[str, Dict[str, float]] = field(default_factory=dict)
    # P0-E: {session_id: {(symbol, tier): pnl}} — tier 归因的日盈亏
    symbol_tier_daily_pnl: Dict[str, Dict[str, float]] = field(default_factory=dict)
    symbol_frozen_set: Dict[str, set] = field(default_factory=dict)
    # P0-E: {session_id: {symbol: set(tier)}} — 各 symbol 被冻结的 tier
    symbol_frozen_tiers: Dict[str, Dict[str, set]] = field(default_factory=dict)
    strat_pause_meta: Dict[Any, Dict[str, Any]] = field(default_factory=dict)
    defensive_entered_at: Dict[str, float] = field(default_factory=dict)
    recovery_until: Dict[str, float] = field(default_factory=dict)
    state_lock: Any = None
    SYMBOL_FREEZE_COOLDOWN_MINUTES: float = 60.0
    PEAK_DECAY_GRACE_HOURS: float = 2.0
    PEAK_DECAY_RATE_PER_HOUR: float = 0.10
    PEAK_DECAY_ACCEL_HOURS: float = 6.0
    RECOVERY_DURATION_HOURS: float = 2.0
    RECOVERY_POSITION_SCALE: float = 0.5

    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    get_lock_profile: Callable = field(repr=False, default=lambda *a, **k: None)
    paper_loss_locks_disabled: Callable = field(repr=False, default=lambda *a, **k: False)
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    should_log_pause_event: Callable = field(repr=False, default=lambda *a, **k: True)
    record_strategy_pause: Callable = field(repr=False, default=lambda *a, **k: None)
    clear_strategy_pause_meta: Callable = field(repr=False, default=lambda *a, **k: None)


def build_symbol_risk_host(svc) -> SymbolRiskHost:
    return SymbolRiskHost(
        symbol_daily_pnl=getattr(svc, "_symbol_daily_pnl", None) or {},
        symbol_tier_daily_pnl=getattr(svc, "_symbol_tier_daily_pnl", None) or {},
        symbol_frozen_set=getattr(svc, "_symbol_frozen_set", None) or {},
        symbol_frozen_tiers=getattr(svc, "_symbol_frozen_tiers", None) or {},
        strat_pause_meta=getattr(svc, "_strat_pause_meta", None) or {},
        defensive_entered_at=svc._defensive_entered_at,
        recovery_until=svc._recovery_until,
        state_lock=getattr(svc, "_state_lock", None),
        SYMBOL_FREEZE_COOLDOWN_MINUTES=float(getattr(svc, "_SYMBOL_FREEZE_COOLDOWN_MINUTES", 60) or 60),
        PEAK_DECAY_GRACE_HOURS=svc._PEAK_DECAY_GRACE_HOURS,
        PEAK_DECAY_RATE_PER_HOUR=svc._PEAK_DECAY_RATE_PER_HOUR,
        PEAK_DECAY_ACCEL_HOURS=svc._PEAK_DECAY_ACCEL_HOURS,
        RECOVERY_DURATION_HOURS=svc._RECOVERY_DURATION_HOURS,
        RECOVERY_POSITION_SCALE=svc._RECOVERY_POSITION_SCALE,
        get_trading_account_id=svc._get_trading_account_id,
        get_lock_profile=svc._get_lock_profile,
        paper_loss_locks_disabled=svc._paper_loss_locks_disabled,
        append_event=svc._append_event,
        should_log_pause_event=svc._should_log_pause_event,
        record_strategy_pause=svc._record_strategy_pause,
        clear_strategy_pause_meta=svc._clear_strategy_pause_meta,
    )


def evaluate_dynamic_risk(session, market_summary: Dict[str, Any], host: SymbolRiskHost) -> None:
    risk_mode = getattr(session, "risk_mode", None) or "ai_dynamic"

    vol_scores = []
    for sym, info in market_summary.items():
        vr = info.get("volatility_regime", "normal")
        score_map = {"low": 0, "normal": 1, "high": 2, "extreme": 3}
        vol_scores.append(score_map.get(vr, 1))

    if not vol_scores:
        return

    avg_vol = sum(vol_scores) / len(vol_scores)
    max_dd = session.max_drawdown or 0

    if avg_vol >= 2.5 or max_dd > 0.12:
        ai_level = "conservative"
        reason = f"高波动({avg_vol:.1f}) 或 回撤过大({max_dd:.1%}) → 收紧风控"
    elif avg_vol >= 1.5 or max_dd > 0.06:
        ai_level = "moderate"
        reason = f"中等波动({avg_vol:.1f}) 回撤可控({max_dd:.1%}) → 均衡风控"
    else:
        ai_level = "aggressive" if risk_mode == "aggressive" else "moderate"
        reason = f"低波动({avg_vol:.1f}) 回撤极小({max_dd:.1%}) → 适度放开"

    if risk_mode == "conservative" and ai_level == "aggressive":
        ai_level = "moderate"
        reason += "（偏保守约束）"
    elif risk_mode == "aggressive" and ai_level == "conservative":
        ai_level = "moderate"
        reason += "（偏激进约束）"

    prev_level = session.risk_level
    session.risk_level = ai_level

    vol_label = "extreme" if avg_vol >= 2.5 else "high" if avg_vol >= 1.5 else "low" if avg_vol < 0.5 else "normal"
    assessment = {
        "effective_level": ai_level,
        "reason": reason,
        "market_volatility": vol_label,
        "adjusted_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        session.current_risk_assessment = assessment
    except Exception:
        pass

    if prev_level != ai_level:
        host.append_event(session, "risk_adjusted",
                           f"AI 风控动态调整: {prev_level} → {ai_level} ({reason})")
        logger.info(f"[FullAuto] 动态风险: {prev_level} → {ai_level}: {reason}")


def update_symbol_daily_pnl(db: Session, session, host: SymbolRiskHost) -> None:
    sid = session.session_id
    try:
        trading_acct = host.get_trading_account_id(db, session)
        # paper_orders.created_at is stored as local naive timestamp in the
        # current schema, so use local naive cutoff values for comparison.
        today_start = datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        cutoff_start = today_start
        try:
            events = session.event_log or []
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                if ev.get("event") not in ("risk_reset", "old_positions_cleanup"):
                    continue
                raw_ts = str(ev.get("time") or "").strip()
                if not raw_ts:
                    continue
                # 2026-06-17: 修复 tzinfo 处理。旧代码用 datetime.now().astimezone().tzinfo
                # 给 naive reset_ts 赋偏移，在容器 TZ 未设时会得到错误偏移；且
                # astimezone()/replace(tzinfo=None) 多次转换易引入 naive/aware 混用 TypeError。
                # DB 的 created_at 是 local naive，cutoff 也必须保持 local naive。
                # 此处统一把 reset_ts 归一成 local naive：aware 先转本地再剥 tz，naive 直接用。
                reset_ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                if reset_ts.tzinfo is not None:
                    # aware（通常带 Z/UTC）→ 转本地时区 → 剥 tz 成 naive，与 created_at 对齐
                    reset_ts = reset_ts.astimezone().replace(tzinfo=None)
                # reset_ts 现在是 local naive，与 cutoff_start(today_start) 同维度，可安全比较
                if reset_ts > cutoff_start:
                    cutoff_start = reset_ts
        except Exception:
            cutoff_start = today_start
        result = db.execute(sa_text("""
            SELECT symbol,
                   SUM(CASE WHEN pnl IS NOT NULL THEN pnl ELSE 0 END) as total_pnl
            FROM paper_orders
            WHERE account_id = :acct_id
              AND status = 'filled'
              AND created_at >= :cutoff_start
              AND COALESCE(close_reason, '') NOT IN ('old_position_cleanup', 'smoke_test_cleanup')
            GROUP BY symbol
        """), {
            "acct_id": trading_acct,
            "cutoff_start": cutoff_start,
        })
        rows = result.fetchall()
        pnl_map = {}
        for row in rows:
            sym = (row[0] or "").upper().replace("/USDT", "").replace("/USDC", "")
            if sym:
                pnl_map[sym] = float(row[1] or 0)

        # P0-E: tier 归因的日盈亏（XPL 中线亏只冻 XPL 中线，不冻 XPL 短线/长线）
        # 归因优先级：trade_nature(NATURE_TO_TIER) > AIStrategy.timeframe_tier
        # （后者 server_default='mid'，会把 legacy 短线单误归入中线）
        _tier_rows = db.execute(sa_text("""
            SELECT symbol,
                   COALESCE(
                     CASE o.trade_nature
                       WHEN 'scalp' THEN 'short'
                       WHEN 'intraday' THEN 'short'
                       WHEN 'swing' THEN 'mid'
                       WHEN 'trend_follow' THEN 'long'
                       WHEN 'position' THEN 'long'
                       ELSE NULL
                     END,
                     s.timeframe_tier,
                     'unknown'
                   ) AS tier,
                   SUM(CASE WHEN o.pnl IS NOT NULL THEN o.pnl ELSE 0 END) as total_pnl
            FROM paper_orders o
            LEFT JOIN ai_strategies s ON s.strategy_id = o.strategy_id
            WHERE o.account_id = :acct_id
              AND o.status = 'filled'
              AND o.created_at >= :cutoff_start
              AND COALESCE(o.close_reason, '') NOT IN ('old_position_cleanup', 'smoke_test_cleanup')
            GROUP BY 1, 2
        """), {
            "acct_id": trading_acct,
            "cutoff_start": cutoff_start,
        }).fetchall()
        tier_pnl_map: Dict[str, float] = {}
        for row in _tier_rows:
            _sym = (row[0] or "").upper().replace("/USDT", "").replace("/USDC", "")
            _tier = str(row[1] or "unknown").strip().lower()
            if _sym and _tier in ("short", "mid", "long"):
                tier_pnl_map[f"{_sym}|{_tier}"] = float(row[2] or 0)

        with host.state_lock:
            host.symbol_daily_pnl[sid] = pnl_map
            host.symbol_tier_daily_pnl[sid] = tier_pnl_map
        if pnl_map:
            loss_symbols = [s for s, p in pnl_map.items() if p < 0]
            if loss_symbols:
                logger.info(
                    f"[PerSymbolRisk] {sid} 日亏损追踪: " +
                    ", ".join(f"{s}=${p:.0f}" for s, p in pnl_map.items() if p < 0)
                )
        _tier_loss = [(k, v) for k, v in tier_pnl_map.items() if v < 0]
        if _tier_loss:
            logger.info(
                "[PerSymbolRisk] %s 日亏损(tier归因): %s",
                sid,
                ", ".join(f"{k.split('|')[0]}[{k.split('|')[1]}]=${v:.0f}" for k, v in _tier_loss),
            )
    except Exception as e:
        logger.debug(f"[PerSymbolRisk] 日亏损追踪查询失败(非致命): {e}")

def _strategy_tier(strat) -> str:
    """策略周期归因：timeframe_tier > genome.trade_nature。未知回退 ''。"""
    try:
        from backend.services.sub_position_manager import NATURE_TO_TIER
    except Exception:
        NATURE_TO_TIER = {}
    _t = str(getattr(strat, "timeframe_tier", None) or "").strip().lower()
    if _t in ("short", "mid", "long"):
        return _t
    _genome = getattr(strat, "genome", None)
    if isinstance(_genome, dict):
        _t = NATURE_TO_TIER.get(str(_genome.get("trade_nature") or "").strip().lower(), "")
        if _t in ("short", "mid", "long"):
            return _t
    return ""


def freeze_symbol_strategies(db: Session, session, symbol: str, reason: str,
                             host: SymbolRiskHost, tiers: Optional[List[str]] = None) -> None:
    """P0-E: 冻结某 symbol 的策略。tiers 给定时只冻结这些周期（tier 隔离）；
    tiers=None 表示冻结全部周期（旧行为 / 情绪层等 symbol 级信号）。"""
    if host.paper_loss_locks_disabled(session):
        return
    from backend.database.models import AIStrategy
    sid = session.session_id
    with host.state_lock:
        frozen = host.symbol_frozen_set.setdefault(sid, set())
        if symbol in frozen:
            return  # 已冻结，跳过
        frozen.add(symbol)
        if tiers:
            host.symbol_frozen_tiers.setdefault(sid, {})[symbol] = set(tiers)

    # 暂停该 symbol 的 active 策略（tiers 给定时只暂停对应周期）
    sids = list(session.active_strategy_ids or [])
    paused_count = 0
    for strat_id in sids:
        strat = db.query(AIStrategy).filter(
            AIStrategy.strategy_id == strat_id,
            AIStrategy.primary_symbol == symbol,
            AIStrategy.status == "active",
        ).first()
        if not strat:
            continue
        if tiers and _strategy_tier(strat) not in set(tiers):
            continue  # P0-E: 其他周期照常，绝不连坐
        strat.status = "paused"
        host.record_strategy_pause(
            strat.strategy_id, f"日亏损冻结:{reason[:40]}", by="per_symbol_risk"
        )
        # 2026-06-19: 统一注册到 SymbolLockRegistry
        from backend.services.symbol_lock_registry import lock_registry
        lock_registry.lock(symbol, strategy_id=str(strat.strategy_id),
                           reason_code="per_symbol_loss", by="per_symbol_risk")
        paused_count += 1
    try:
        db.flush()
    except Exception as _flush_err:
        logger.warning(f"[FullAuto] freeze_symbol flush 失败，rollback: {_flush_err}")
        try:
            db.rollback()
        except Exception:
            pass

    # 同步冻结 MTOrchestrator：仅 symbol 级全周期冻结时才冻结（tier 限定时跳过，
    # 避免 MTOrchestrator 的 symbol 级 _freeze_until 连坐其他周期）
    if not tiers:
        try:
            from backend.services.multi_timeframe_orchestrator import mt_orchestrator
            mt_orchestrator._freeze_until[symbol] = time.time() + host.SYMBOL_FREEZE_COOLDOWN_MINUTES * 60
            mt_orchestrator._freeze_reason[symbol] = reason[:60]
        except Exception:
            pass

    if host.should_log_pause_event(session.session_id, f"sym_freeze:{symbol}"):
        host.append_event(session, "symbol_loss_freeze",
            f"[PerSymbolRisk] {symbol} 冻结: {reason} (暂停{paused_count}个策略)")
    logger.warning(f"[PerSymbolRisk] {symbol} 冻结: {reason} (暂停{paused_count}个策略)")
    # [2026-08-15 收敛] 冻结事件统一登记到 FreezeCoordinator 台账（单一可见入口）
    try:
        from backend.services.risk_management.freeze_coordinator import register_event
        register_event("freeze_per_symbol", int(getattr(session, "account_id", 0) or 0),
                       "per_symbol_risk", symbol, f"{reason} (paused={paused_count})")
    except Exception as _reg_err:
        logger.debug("[PerSymbolRisk] 台账登记失败(非致命): %s", _reg_err)

def unfreeze_recovered_symbols(db: Session, session, still_frozen: List[str], host: SymbolRiskHost) -> None:
    from backend.database.models import AIStrategy
    sid = session.session_id
    frozen = host.symbol_frozen_set.get(sid, set())
    if not frozen:
        return

    still_frozen_upper = {s.upper() for s in still_frozen}
    to_unfreeze = {s for s in frozen if s.upper() not in still_frozen_upper}
    if not to_unfreeze:
        return

    for symbol in to_unfreeze:
        # P0-E: 只恢复「本 symbol 被冻结的周期」策略；未记录 = 旧行为恢复全部
        _frozen_tiers = (host.symbol_frozen_tiers.get(sid, {}) or {}).pop(symbol, None)
        # 恢复该 symbol 的 paused 策略
        sids = list(session.active_strategy_ids or []) + list(session.terminated_strategy_ids or [])
        resumed = 0
        for strat_id in sids:
            strat = db.query(AIStrategy).filter(
                AIStrategy.strategy_id == strat_id,
                AIStrategy.primary_symbol == symbol,
                AIStrategy.status == "paused",
            ).first()
            if not strat:
                continue
            if _frozen_tiers and _strategy_tier(strat) not in _frozen_tiers:
                continue  # 其他周期的 paused 策略与本 symbol 冻结无关，不动
            _meta = host.strat_pause_meta.get(int(strat.strategy_id), {})
            if "震荡市" in str(_meta.get("reason") or ""):
                continue
            strat.status = "active"
            host.clear_strategy_pause_meta(strat.strategy_id)
            resumed += 1
        try:
            db.flush()
        except Exception as _dbf_err:
            logger.warning(f"[FullAuto] db.flush failed: {_dbf_err}")
            try:
                db.rollback()
            except Exception:
                pass

        # 解冻 MTOrchestrator
        try:
            from backend.services.multi_timeframe_orchestrator import mt_orchestrator
            mt_orchestrator._freeze_until.pop(symbol, None)
            mt_orchestrator._freeze_reason.pop(symbol, None)
        except Exception:
            pass

        if resumed and host.should_log_pause_event(session.session_id, f"sym_unfreeze:{symbol}"):
            host.append_event(session, "symbol_loss_unfreeze",
                f"[PerSymbolRisk] {symbol} 解冻: 日亏损已恢复 (恢复{resumed}个策略)")
        logger.info(f"[PerSymbolRisk] {symbol} 解冻: 日亏损已恢复")
        # [2026-08-15 收敛] 解冻事件统一登记
        try:
            from backend.services.risk_management.freeze_coordinator import register_event
            register_event("unfreeze_per_symbol", int(getattr(session, "account_id", 0) or 0),
                           "per_symbol_risk", symbol, f"日亏损已恢复 (resumed={resumed})")
        except Exception:
            pass

    frozen.difference_update(to_unfreeze)

def check_per_symbol_risk(db: Session, session, host: SymbolRiskHost) -> PerSymbolRiskResult:
    result = PerSymbolRiskResult()
    profile = host.get_lock_profile(session)
    if profile.disable_loss_locks:
        return result
    sid = session.session_id
    symbol_loss_pct = profile.symbol_daily_loss_pct
    global_dd = profile.global_extreme_drawdown
    global_daily = profile.global_extreme_daily_loss_pct

    # ── Layer 1: per-symbol 日亏损检查（P0-E: tier 归因，只冻亏损所属周期）──
    symbol_pnl = host.symbol_daily_pnl.get(sid, {})
    tier_pnl = host.symbol_tier_daily_pnl.get(sid, {})
    total_equity = 0.0
    try:
        trading_acct = host.get_trading_account_id(db, session)
        from backend.services.paper_trading_engine import paper_engine
        bal = paper_engine.get_balance(db, trading_acct) or {}
        total_equity = float(bal.get("total_equity", 0))
    except Exception:
        pass
    if total_equity <= 0:
        total_equity = 10000.0  # fallback

    if tier_pnl:
        # tier 归因数据可用：按 (symbol, tier) 精确冻结（绝不跨周期）
        tier_loss_items = [(k, p) for k, p in tier_pnl.items() if p < 0]
        for k, pnl in tier_loss_items:
            _sym, _tier = k.split("|", 1)
            loss_pct = abs(pnl) / total_equity
            if loss_pct < symbol_loss_pct:
                continue
            if _sym not in result.frozen_symbols:
                result.frozen_symbols.append(_sym)
                result.frozen_symbol_tiers[_sym] = []
            if _tier not in result.frozen_symbol_tiers[_sym]:
                result.frozen_symbol_tiers[_sym].append(_tier)
            _prev = result.symbol_reasons.get(_sym)
            _new = (
                f"[{_tier}]日亏损 ${abs(pnl):.0f} ({loss_pct*100:.1f}% 权益) "
                f"超过阈值 {symbol_loss_pct*100:.0f}%"
            )
            result.symbol_reasons[_sym] = "; ".join(x for x in (_prev, _new) if x)
    else:
        # tier 归因不可用（旧数据）→ 回退旧行为：symbol 级整体判断（冻结全部周期）
        for symbol, pnl in symbol_pnl.items():
            if pnl >= 0:
                continue  # 该 symbol 盈利或持平
            loss_pct = abs(pnl) / total_equity
            if loss_pct >= symbol_loss_pct:
                result.frozen_symbols.append(symbol)
                result.symbol_reasons[symbol] = (
                    f"日亏损 ${abs(pnl):.0f} ({loss_pct*100:.1f}% 权益) "
                    f"超过阈值 {symbol_loss_pct*100:.0f}%"
                )

    # ── Layer 2: 全局极端安全网 ──
    # 2a. 总回撤超过 50%
    current_dd = getattr(session, "current_drawdown", None) or 0
    if current_dd > global_dd:
        result.global_freeze = True
        result.global_reason = (
            f"极端回撤 {current_dd*100:.1f}% > 安全网 {global_dd*100:.0f}%"
        )
        return result

    # 2b. 全局日亏损超过阈值
    total_daily_loss = sum(p for p in symbol_pnl.values() if p < 0)
    if total_equity > 0 and abs(total_daily_loss) / total_equity >= global_daily:
        result.global_freeze = True
        result.global_reason = (
            f"极端日亏损 {abs(total_daily_loss):.0f} "
            f"({abs(total_daily_loss)/total_equity*100:.1f}%) "
            f"> 安全网 {global_daily*100:.0f}%"
        )
        return result

    # ── Layer 3: 情报极端事件（保留原逻辑）──
    try:
        market_summary = session.last_market_summary or {}
        for symbol, info in market_summary.items():
            if isinstance(info, dict):
                si = info.get("sentiment_index", 50)
                if si < 10:
                    result.frozen_symbols.append(symbol)
                    result.symbol_reasons[symbol] = (
                        f"极度恐惧(情绪{si:.0f})，市场可能恐慌性抛售"
                    )
    except Exception:
        pass

    return result

def check_global_risk(db: Session, session, host: SymbolRiskHost) -> Optional[str]:
    sid = session.session_id
    max_dd = session.max_total_drawdown_pct or 0.30
    current_dd = getattr(session, "current_drawdown", None) or 0

    # ── 峰值衰减：防守模式下逐步降低 peak_balance ──
    if session.status == "defensive":
        now_ts = time.time()
        entered_ts = host.defensive_entered_at.get(sid)
        if entered_ts is None:
            host.defensive_entered_at[sid] = now_ts
            entered_ts = now_ts

        hours_in_defensive = (now_ts - entered_ts) / 3600
        if hours_in_defensive > host.PEAK_DECAY_GRACE_HOURS:
            decay_hours = hours_in_defensive - host.PEAK_DECAY_GRACE_HOURS
            peak = session.peak_balance or 0
            initial_cap = 10000.0
            current_equity = None
            try:
                from backend.database.models import PaperBalance
                # P5-fix(2026-05-08): paper 模式下读 paper_account 的余额
                _bal_acct = (
                    host.get_trading_account_id(db, session)
                    if (session.trading_mode or "paper") == "paper"
                    else session.account_id
                )
                pb = db.query(PaperBalance).filter(
                    PaperBalance.account_id == _bal_acct
                ).first()
                if pb:
                    initial_cap = float(pb.initial_balance or 10000)
                    current_equity = float(pb.total_equity or initial_cap)
            except Exception:
                pass
            if current_equity is None:
                current_equity = initial_cap + (session.total_pnl or 0)
            gap = peak - current_equity
            if gap > 0:
                # 加速衰减：防守超过6小时后衰减率×3，避免长期死锁
                effective_rate = host.PEAK_DECAY_RATE_PER_HOUR
                accel_tag = ""
                if hours_in_defensive > host.PEAK_DECAY_ACCEL_HOURS:
                    effective_rate *= 3
                    accel_tag = " [加速×3]"
                decay_amount = gap * effective_rate * decay_hours
                new_peak = max(current_equity, peak - decay_amount)
                if new_peak < peak:
                    session.peak_balance = round(new_peak, 4)
                    new_dd = (new_peak - current_equity) / new_peak if new_peak > 0 else 0
                    session.current_drawdown = max(0, new_dd)
                    current_dd = session.current_drawdown
                    logger.info(
                        f"[FullAuto] 峰值衰减{accel_tag}: peak ${peak:.1f}→${new_peak:.1f}, "
                        f"DD {current_dd*100:.1f}% (防守{hours_in_defensive:.1f}h)")

    if current_dd > max_dd:
        return f"当前回撤 {current_dd*100:.1f}% 超过上限 {max_dd*100:.0f}%"

    # 情报驱动的风控：检查是否有极端市场信号
    try:
        market_summary = session.last_market_summary or {}
        for symbol, info in market_summary.items():
            if isinstance(info, dict):
                si = info.get("sentiment_index", 50)
                if si < 10:
                    return f"[WARN] {symbol} 极度恐惧(情绪{si:.0f})，市场可能恐慌性抛售"
    except Exception:
        pass

    return None

    # ══════════════════════════════════════════════════
    #  多路分析师系统 — 核心决策流程
    # ══════════════════════════════════════════════════
