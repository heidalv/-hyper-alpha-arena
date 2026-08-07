"""策略生命周期 — champion/terminate/adapt 从 monolith 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

REGIME_PARAM_PROFILES = {
    "trending": {
        "position_cap_pct": 0.80,
        "confidence_threshold": 55,
        "sl_mult": 1.3,
        "trade_freq_mult": 1.0,
    },
    "ranging": {
        "position_cap_pct": 0.40,
        "confidence_threshold": 70,
        "sl_mult": 0.8,
        "trade_freq_mult": 0.6,
    },
    "volatile": {
        "position_cap_pct": 0.30,
        "confidence_threshold": 75,
        "sl_mult": 0.6,
        "trade_freq_mult": 0.4,
    },
    "crisis": {
        "position_cap_pct": 0.10,
        "confidence_threshold": 85,
        "sl_mult": 0.4,
        "trade_freq_mult": 0.2,
    },
}

@dataclass
class StrategyLifecycleHost:
    NATURE_TO_TIER_MAP: Dict[str, str] = field(default_factory=dict)


def build_strategy_lifecycle_host(svc) -> StrategyLifecycleHost:
    return StrategyLifecycleHost(
        NATURE_TO_TIER_MAP=getattr(svc, "_NATURE_TO_TIER_MAP", {}) or {},
    )


def is_champion_strategy(mem) -> bool:
    if not mem:
        return False
    return (
        (mem.total_trades or 0) >= 15
        and (mem.win_rate or 0) >= 0.55
        and (mem.sharpe_ratio or 0) >= 0.5
        and (mem.max_drawdown or 1.0) <= 0.15
    )

def should_terminate_strategy(db: Session, strategy, session) -> tuple:
    from backend.database.models import StrategyMemory

    # 先计算策略年龄（用于后续多级淘汰判决）
    _age_secs = 0
    act_at = None
    if strategy.activated_at:
        act_at = strategy.activated_at
        if act_at.tzinfo is None:
            act_at = act_at.replace(tzinfo=timezone.utc)
        _age_secs = (datetime.now(timezone.utc) - act_at).total_seconds()

    # Phase 3B: 低频淘汰至少观察 7 天（session 里若配 3600 会导致「创建即被误杀」）
    _MIN_LOWFREQ_EVAL_SECS = 7 * 86400
    _NEW_STRATEGY_GRACE_SECS = 48 * 3600  # 48h 内不因低频/僵尸规则淘汰
    min_life = max(int(session.strategy_min_lifetime or 0), _MIN_LOWFREQ_EVAL_SECS)
    if _age_secs < min_life:
        return False, ""

    mem = db.query(StrategyMemory).filter(
        StrategyMemory.strategy_id == strategy.strategy_id
    ).first()

    # 新策略保护期：健康检查/LLM 慢时避免「刚创建→1h后0笔→立刻终止」恶性循环
    if _age_secs < _NEW_STRATEGY_GRACE_SECS:
        return False, ""

    genome = getattr(strategy, "genome", None) or {}
    if isinstance(genome, dict) and "golden_frozen" in (genome.get("tags") or []):
        return False, ""

    # ── 僵尸策略淘汰：活跃 > 3 天但从未交易 → 立即终止释放名额 ──
    _total_trades = mem.total_trades if mem else 0
    _zombie_age_secs = 3 * 86400  # 3 天
    if _age_secs > _zombie_age_secs and _total_trades == 0:
        _days = int(_age_secs / 86400)
        return True, f"僵尸策略（创建{_days}天从未交易，释放策略名额）"

    # ── 低频策略淘汰：活跃 > 7 天但交易 < 3 次 → 视为无效覆盖 ──
    if _age_secs > min_life and _total_trades < 3:
        _days = int(_age_secs / 86400)
        return True, f"低频策略（创建{_days}天仅{_total_trades}次交易，无法评估有效性）"

    if not mem or _total_trades < 3:
        return False, ""

    # 从策略基因组读取自适应阈值，或使用默认值
    genome = getattr(strategy, "genome", None) or {}
    min_wr = genome.get("min_win_rate", 0.30)
    max_dd = genome.get("max_drawdown", 0.25)
    max_loss_ratio = genome.get("max_loss_ratio", 2.0)

    if mem.total_trades >= 50 and (mem.win_rate or 0) < min_wr:  # Phase 3B: 10笔 → 50笔
        return True, f"胜率过低 ({(mem.win_rate or 0)*100:.0f}%，阈值{min_wr*100:.0f}%，交易{mem.total_trades}次)"

    if (mem.max_drawdown or 0) > max_dd:
        return True, f"回撤过大 ({mem.max_drawdown*100:.0f}%，阈值{max_dd*100:.0f}%)"

    avg_profit = abs(mem.avg_profit or 0)
    if mem.total_trades >= 30 and (mem.win_rate or 0) < min_wr + 0.05 and avg_profit > 0 and (mem.avg_loss or 0) > avg_profit * max_loss_ratio:  # Phase 3B: 8笔→30笔
        return True, f"亏损模式持续（胜率{(mem.win_rate or 0)*100:.0f}%，平均亏损远大于盈利）"

    return False, ""

def pause_champion_strategy(db: Session, strategy, reason: str) -> None:
    strategy.status = "paused"
    # 2026-06-19: 统一注册到 SymbolLockRegistry
    try:
        from backend.services.symbol_lock_registry import lock_registry
        lock_registry.lock(
            strategy.primary_symbol or "", strategy_id=str(strategy.strategy_id),
            reason_code="champion_pause", by="champion_eval",
        )
    except Exception:
        pass
    genome = dict(strategy.genome or {})
    tags = list(genome.get("tags") or [])
    if "champion_protected" not in tags:
        tags.append("champion_protected")
    genome["tags"] = tags
    genome["pause_reason"] = reason
    strategy.genome = genome
    db.flush()

def snapshot_strategy_genome(db: Session, strategy, memory) -> None:
    import uuid
    from backend.database.models import StrategyTemplate
    from backend.services.strategy_library import build_promoted_strategy_config

    try:
        cfg = build_promoted_strategy_config(strategy, memory)
        cfg["snapshot_reason"] = "pre_terminate"
        tpl_id = f"tpl_snap_{uuid.uuid4().hex[:8]}"
        snap = StrategyTemplate(
            template_id=tpl_id,
            name=f"[快照] {strategy.name}",
            description=f"策略 {strategy.strategy_id} 终止前快照",
            category=(strategy.prompt_variables or {}).get("trading_style", "trend"),
            market_regime="all",
            tier=getattr(strategy, "timeframe_tier", None) or "mid",
            timeframe=strategy.timeframe or "15m",
            strategy_config=cfg,
            source="snapshot",
            is_active=False,
            rating=3.0,
            tags=["genome_snapshot"],
        )
        db.add(snap)
        db.flush()
        logger.info(f"[FullAuto] 策略 {strategy.strategy_id} 终止前快照 → {tpl_id}")
    except Exception as exc:
        logger.warning(f"[FullAuto] genome 快照失败(非致命): {exc}")

def terminate_strategy(db: Session, strategy, reason: str, host: StrategyLifecycleHost) -> None:
    from backend.services.autonomous_strategy_service import autonomous_service

    strategy.status = "terminated"
    try:
        autonomous_service.unregister_strategy(strategy.strategy_id)
    except Exception as e:
        logger.warning(f"[FullAuto] 注销策略循环失败: {e}")
    db.flush()

    # 设置平仓冷却期：使用 reentry_cooldown 按 tier 隔离 (F1-5)
    symbol = strategy.primary_symbol
    account_id = strategy.account_id
    if symbol and account_id:
        # 从策略提取 tier 和方向
        genome = getattr(strategy, "genome", None) or {}
        _strat_tier = (
            getattr(strategy, "timeframe_tier", None)
            or host.NATURE_TO_TIER_MAP.get(genome.get("trade_nature", ""), "mid")
        )
        _side = genome.get("direction", "long")
        _position_side = "long" if _side in ("buy", "long") else "short"
        try:
            from backend.services.reentry_cooldown import record_full_close
            record_full_close(
                account_id=account_id,
                symbol=symbol,
                position_side=_position_side,
                tier=_strat_tier,
                is_master_close=False,
                close_pnl=0.0,
            )
            logger.info(
                f"[FullAuto] {symbol} 策略终止，已记录{_strat_tier}tier平仓冷却"
            )
        except Exception as _cd_err:
            logger.warning(f"[FullAuto] reentry_cooldown 记录失败: {_cd_err}")

_prev_regime_profile: Optional[dict] = None


def get_regime_profile(regime: str) -> dict:
    global _prev_regime_profile
    profile = REGIME_PARAM_PROFILES.get(regime)
    if not profile:
        profile = REGIME_PARAM_PROFILES.get("ranging", {})

    if _prev_regime_profile is None:
        _prev_regime_profile = profile
        return profile

    prev = _prev_regime_profile
    blend_alpha = 0.3
    blended = {}
    for key in profile:
        old_val = prev.get(key, profile[key])
        new_val = profile[key]
        if isinstance(new_val, (int, float)):
            blended[key] = old_val * (1 - blend_alpha) + new_val * blend_alpha
        else:
            blended[key] = new_val
    _prev_regime_profile = blended
    return blended


def adapt_strategy_params(db: Session, strategy, market_info: dict) -> bool:
    from backend.database.models import StrategyMemory

    sid = strategy.strategy_id
    symbol = strategy.primary_symbol or "?"
    genome = strategy.genome or {}

    # ── 灰度发布路由：检查该策略+币种是否有灰度计划 ──
    try:
        from backend.services.qaa_evolution_bridge import qaa_bridge
        _gs_genome = qaa_bridge.get_genome_for_symbol(sid, symbol)
        if _gs_genome is not None:
            genome = _gs_genome  # 使用灰度路由后的 genome
    except Exception:
        pass  # QAA 不可用时用原始 genome

    # Regime 感知（叠加到现有自适应逻辑）
    mkt_cycle = (market_info or {}).get("market_cycle", "ranging") or "ranging"
    regime_profile = get_regime_profile(mkt_cycle)

    # 读取创建时的基准参数
    base_leverage = genome.get("default_leverage", strategy.default_leverage or 8)
    base_max_leverage = genome.get("max_leverage", strategy.max_leverage or 20)
    base_position_size = genome.get("max_position_size", strategy.max_position_size or 0.1)
    base_sl_pct = genome.get("stop_loss_pct", strategy.stop_loss_pct or 0.05)
    base_tp_pct = genome.get("take_profit_pct", strategy.take_profit_pct or 0.10)

    # Dynamic adaptation coefficients (continuous, replaces rigid _TIER_ADAPT)
    _hold_h = float(genome.get("expected_hold_hours", 0) or 0)
    if _hold_h <= 0:
        _nature = (genome.get("trade_nature")
                   or getattr(strategy, "timeframe_tier", None) or "swing")
        _hold_h = {"scalp": 2, "intraday": 6, "short": 4, "swing": 24,
                   "mid": 24, "position": 96, "long": 168,
                   "trend_follow": 336}.get(_nature, 24)
    _nature_label = (genome.get("trade_nature")
                     or getattr(strategy, "timeframe_tier", None) or "swing")
    _t = min(1.0, max(0.0, (_hold_h - 1) / 167))
    tc = {
        "vol_extreme": round(0.3 + _t * 0.2, 3),
        "vol_high":    round(0.55 + _t * 0.2, 3),
        "vol_low":     round(1.2 - _t * 0.1, 3),
        "sl_extreme":  round(1.5 + _t * 0.5, 3),
        "sl_high":     round(1.2 + _t * 0.4, 3),
        "sl_low":      round(0.85 + _t * 0.05, 3),
        "tp_trend_strong": round(1.2 + _t * 0.8, 3),
        "tp_trend_weak":   round(0.6 + _t * 0.2, 3),
        "sl_range": (round(0.008 + _t * 0.012, 4), round(0.08 + _t * 0.12, 4)),
        "tp_range": (round(0.01 + _t * 0.04, 4), round(0.15 + _t * 0.85, 4)),
        "pos_range": (0.02, round(0.3 + _t * 0.2, 3)),
    }

    mkt = market_info or {}
    vol_regime = mkt.get("volatility_regime", "normal")
    trend_strength = abs(mkt.get("trend_strength", 0))
    sentiment = mkt.get("sentiment_index", 50)

    # ═══════════════════════════════════════════════════
    # 横盘市场保护：ranging/sideways 胜率极低(14.5%)，大幅降仓+提门槛
    # ═══════════════════════════════════════════════════
    _is_ranging = mkt_cycle in ("sideways", "ranging", "transition")

    mem = db.query(StrategyMemory).filter(
        StrategyMemory.strategy_id == sid
    ).first()

    win_rate = (mem.win_rate or 0) if mem else 0
    total_trades = (mem.total_trades or 0) if mem else 0
    max_dd = (mem.max_drawdown or 0) if mem else 0
    avg_loss = abs(mem.avg_loss or 0) if mem else 0
    avg_profit = abs(mem.avg_profit or 0) if mem else 0

    changes = []

    # ── 1. 杠杆自适应（短线对波动更敏感）──
    lev_mult = 1.0
    if vol_regime == "extreme":
        lev_mult *= tc["vol_extreme"]
        changes.append(f"极端波动[{_nature_label}]→杠杆×{tc['vol_extreme']}")
    elif vol_regime == "high":
        lev_mult *= tc["vol_high"]
        changes.append(f"高波动[{_nature_label}]→杠杆×{tc['vol_high']}")
    elif vol_regime == "low":
        lev_mult *= tc["vol_low"]

    if max_dd > 0.20:
        lev_mult *= 0.5
        changes.append(f"回撤{max_dd*100:.0f}%→杠杆×0.5")
    elif max_dd > 0.12:
        lev_mult *= 0.7

    if total_trades >= 5 and win_rate < 0.35:
        lev_mult *= 0.7
        changes.append(f"胜率{win_rate*100:.0f}%低→杠杆×0.7")
    elif total_trades >= 10 and win_rate > 0.60 and max_dd < 0.08:
        lev_mult *= 1.1

    new_leverage = max(5, min(base_max_leverage, round(base_leverage * lev_mult)))

    # ── 2. 仓位大小自适应 ──
    pos_mult = 1.0
    if vol_regime == "extreme":
        pos_mult *= 0.5
    elif vol_regime == "high":
        pos_mult *= 0.7
    if max_dd > 0.15:
        pos_mult *= 0.6
    elif max_dd > 0.10:
        pos_mult *= 0.8
    if total_trades >= 5 and win_rate < 0.35:
        pos_mult *= 0.7
    # ── 横盘市场降仓：胜率极低，仓位砍半 ──
    if _is_ranging:
        pos_mult *= 0.4
        changes.append("横盘市场→仓位×0.4")
    elif total_trades >= 10 and win_rate > 0.55 and max_dd < 0.08:
        pos_mult *= 1.15

    new_pos_size = round(max(tc["pos_range"][0], min(tc["pos_range"][1], base_position_size * pos_mult)), 4)

    # ── 3. 止损幅度自适应（短线紧、长线宽）──
    sl_mult = 1.0
    if vol_regime == "extreme":
        sl_mult *= tc["sl_extreme"]
        changes.append(f"极端波动[{_nature_label}]→止损×{tc['sl_extreme']}")
    elif vol_regime == "high":
        sl_mult *= tc["sl_high"]
    elif vol_regime == "low":
        sl_mult *= tc["sl_low"]
    if max_dd > 0.15:
        sl_mult *= 0.85

    new_sl_pct = round(max(tc["sl_range"][0], min(tc["sl_range"][1], base_sl_pct * sl_mult)), 4)

    # ── 4. 止盈幅度自适应（短线快兑现、长线放宽）──
    tp_mult = 1.0
    if trend_strength > 0.6:
        tp_mult *= tc["tp_trend_strong"]
        changes.append(f"强趋势[{_nature_label}]→止盈×{tc['tp_trend_strong']}")
    elif trend_strength < 0.2:
        tp_mult *= tc["tp_trend_weak"]
    if vol_regime == "extreme":
        tp_mult *= 1.5
    elif vol_regime == "high":
        tp_mult *= 1.3

    new_tp_pct = round(max(tc["tp_range"][0], min(tc["tp_range"][1], base_tp_pct * tp_mult)), 4)

    # ── 5. 置信度门槛自适应 ──
    base_confidence = genome.get("min_confidence", strategy.min_confidence or 0.6)
    conf_adj = 0.0
    if total_trades >= 5 and win_rate < 0.35:
        conf_adj += 0.10
        changes.append("低胜率→置信门槛提高10%")
    if vol_regime == "extreme":
        conf_adj += 0.05
    if sentiment < 20 or sentiment > 80:
        conf_adj += 0.05
        changes.append("极端情绪→置信门槛+5%")
    # ── 横盘市场提高置信门槛：信号不可靠，需要更高确定性才能开仓 ──
    if _is_ranging:
        conf_adj += 0.15
        changes.append("横盘市场→置信门槛+15%")

    new_confidence = round(max(0.50, min(0.90, base_confidence + conf_adj)), 2)

    # ── 6. Regime 感知覆盖 ──
    regime_conf_min = regime_profile.get("confidence_threshold", 60) / 100.0
    if new_confidence < regime_conf_min:
        new_confidence = round(regime_conf_min, 2)
        changes.append(f"regime={mkt_cycle}→置信门槛≥{regime_conf_min*100:.0f}%")

    regime_pos_cap = regime_profile.get("position_cap_pct", 0.80)
    if new_pos_size > regime_pos_cap:
        new_pos_size = round(regime_pos_cap, 4)
        changes.append(f"regime={mkt_cycle}→仓位上限{regime_pos_cap*100:.0f}%")

    regime_sl_mult = regime_profile.get("sl_mult", 1.0)
    if abs(regime_sl_mult - 1.0) > 0.05:
        new_sl_pct = round(max(tc["sl_range"][0], min(tc["sl_range"][1], new_sl_pct * regime_sl_mult)), 4)
        changes.append(f"regime={mkt_cycle}→止损×{regime_sl_mult:.1f}")

    # ── 检测是否有实际变化 ──
    changed = False
    if abs(new_leverage - (strategy.default_leverage or 0)) >= 1:
        strategy.default_leverage = new_leverage
        changed = True
    if abs(new_pos_size - (strategy.max_position_size or 0)) > 0.005:
        strategy.max_position_size = new_pos_size
        changed = True
    if abs(new_sl_pct - (strategy.stop_loss_pct or 0)) > 0.002:
        strategy.stop_loss_pct = new_sl_pct
        changed = True
    if abs(new_tp_pct - (strategy.take_profit_pct or 0)) > 0.002:
        strategy.take_profit_pct = new_tp_pct
        changed = True
    if abs(new_confidence - (strategy.min_confidence or 0)) > 0.02:
        strategy.min_confidence = new_confidence
        changed = True

    if changed:
        db.flush()
        change_summary = " | ".join(changes[:5]) if changes else "参数微调"
        logger.info(
            f"[FullAuto] 策略自适应 {symbol}/{sid}: "
            f"杠杆={new_leverage}x 仓位={new_pos_size*100:.1f}% "
            f"SL={new_sl_pct*100:.1f}% TP={new_tp_pct*100:.1f}% "
            f"置信={new_confidence*100:.0f}% | {change_summary}"
        )
        return True
    return False
