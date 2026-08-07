"""决策仓位 / 杠杆 / 置信度 — 从 monolith 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Set

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class DecisionSizingHost:
    last_orch_decisions: Dict[str, Any] = field(default_factory=dict)
    pre_screen_passed: Set[str] = field(default_factory=set)
    extract_ai_position_pct: Callable = field(repr=False, default=lambda *a, **k: None)


def build_decision_sizing_host(svc) -> DecisionSizingHost:
    return DecisionSizingHost(
        last_orch_decisions=getattr(svc, "_last_orch_decisions", None) or {},
        pre_screen_passed=getattr(svc, "_pre_screen_passed", None) or set(),
        extract_ai_position_pct=svc._extract_ai_position_pct,
    )


def ai_dynamic_position_pct(
    confidence: int, volatility: float,
    open_position_count: int,
    tier: str = "mid",
    tier_budget_pct: float = 0.0,
) -> float:
    if confidence >= 85:
        base = 0.09
    elif confidence >= 75:
        base = 0.07
    elif confidence >= 65:
        base = 0.06
    elif confidence >= 50:
        base = 0.05
    else:
        base = 0.04

    # tier 系数：short 偏小仓快进快出，long 偏大仓长持
    _tier_scale = {"short": 0.75, "mid": 1.0, "long": 1.15}
    base *= _tier_scale.get(tier, 1.0)

    if volatility >= 0.04:
        vol_adj = 0.70
    elif volatility >= 0.025:
        vol_adj = 0.85
    elif volatility >= 0.015:
        vol_adj = 1.0
    else:
        vol_adj = 1.1

    if open_position_count >= 6:
        count_adj = 0.60
    elif open_position_count >= 4:
        count_adj = 0.70
    elif open_position_count >= 2:
        count_adj = 0.80
    elif open_position_count >= 1:
        count_adj = 0.90
    else:
        count_adj = 1.0

    result = base * vol_adj * count_adj

    # 多周期预算限制：tier_budget_pct 是该 tier 分配到的 equity 比例上限
    if tier_budget_pct > 0:
        result = min(result, tier_budget_pct)

    return max(0.04, min(0.10, round(result, 4)))

    # ══════════════════════════════════════════════════
    #  v3 整改：TradingDecisionInterface 接缝点（Kelly 上限 + DRL 影子建议）
    #  零影响默认：coordinator 未注入或 flag 关闭时完全透传 base_pct。
    # ══════════════════════════════════════════════════

def apply_tdi_position_advice(
    symbol: str,
    base_pct: float,
    confidence: int,
    volatility: float,
    open_position_count: int,
    tier: str = "mid",
    tier_budget_pct: float = 0.0,
    equity: float = 0.0,
    regime: str = "ranging",
    base_direction: str = "hold",
):
    try:
        from backend.services.trading_decision_interface import (
            trading_decision_interface,
            DecisionContext,
        )
        ctx = DecisionContext(
            symbol=symbol,
            tier=tier,
            regime=regime,
            confidence=int(confidence or 0),
            volatility=float(volatility or 0.0),
            open_position_count=int(open_position_count or 0),
            tier_budget_pct=float(tier_budget_pct or 0.0),
            equity=float(equity or 0.0),
        )
        pos_advice = trading_decision_interface.decide_position_pct(
            base_pct=float(base_pct), context=ctx,
        )
        dir_advice = trading_decision_interface.decide_direction(
            base_direction=base_direction, context=ctx,
        )
        # p1-kelly-fa-portfolio: 组合风险前置检查（flag 关闭则自动透传 passed=True）
        risk_verdict = trading_decision_interface.check_portfolio_risk(ctx)
        final_pct = float(pos_advice.position_pct)
        if not risk_verdict.passed:
            # ENABLE_PORTFOLIO_RISK=True 且聚合器判定不通过 → 禁入
            final_pct = 0.0
        meta = {
            "base_pct": float(base_pct),
            "final_pct": final_pct,
            "position_source": pos_advice.source,
            "kelly_upper_bound": pos_advice.kelly_upper_bound,
            "direction_source": dir_advice.source,
            "confidence_weight": dir_advice.confidence_weight,
            "drl_meta": dir_advice.metadata,
            "portfolio_risk": {
                "passed": risk_verdict.passed,
                "risk_level": risk_verdict.risk_level,
                "reason_code": risk_verdict.reason_code,
                "reason_text": risk_verdict.reason_text,
                "portfolio_risk_value": risk_verdict.portfolio_risk,
                "forced_adjustments": list(risk_verdict.forced_adjustments or []),
            },
        }

        # v3 整改: 实时广播到 AI 学习中心（自带节流，避免高频刷屏）
        try:
            from backend.services.ws_broadcast import ws_broadcast_hub
            ws_broadcast_hub.broadcast_drl_update({
                "symbol": symbol,
                "tier": tier,
                "base_direction": base_direction,
                "direction_source": dir_advice.source,
                "confidence_weight": dir_advice.confidence_weight,
                "drl_meta": dir_advice.metadata,
            })
            ws_broadcast_hub.broadcast_kelly_update({
                "symbol": symbol,
                "tier": tier,
                "base_pct": float(base_pct),
                "final_pct": final_pct,
                "kelly_upper_bound": pos_advice.kelly_upper_bound,
                "position_source": pos_advice.source,
                "equity": float(equity or 0.0),
                "portfolio_risk_passed": risk_verdict.passed,
            })
        except Exception:
            pass

        return final_pct, meta
    except Exception as _e:
        # 任何异常都退回 base_pct，保持行为完全兼容
        return float(base_pct), {"base_pct": float(base_pct), "final_pct": float(base_pct), "position_source": "rule_fallback", "error": str(_e)}

def resolve_alignment_scale(sym: str, host: DecisionSizingHost) -> float:
    try:
        _orch_decs = getattr(host, "last_orch_decisions", {}) or {}
        _dec = _orch_decs.get(sym)
        if _dec is None:
            return 1.0
        pm = float(getattr(_dec, "position_multiplier", 1.0) or 1.0)
        # 钳到安全区间：最低 0.5（冲突时砍半），最高 1.2（共振时小幅加仓）
        return max(0.5, min(1.2, pm))
    except Exception:
        return 1.0

def resolve_decision_leverage(
    dec: dict,
    sym: str,
    tier: str,
    mkt: dict,
    db: Session,
    account_id: int,
    trade_nature: str = "",
    market_summary: dict = None,
) -> tuple:
    try:
        from backend.config.settings import DYNAMIC_LEVERAGE_MIN, DYNAMIC_LEVERAGE_MAX
    except ImportError:
        DYNAMIC_LEVERAGE_MIN = 5.0
        DYNAMIC_LEVERAGE_MAX = 20.0

    _explicit = None
    _source = "default"
    for _lev_key in ("leverage", "final_leverage", "recommended_leverage"):
        try:
            _raw_lev = dec.get(_lev_key)
            if _raw_lev is not None and float(_raw_lev) > 0:
                _explicit = float(_raw_lev)
                _source = f"ai:{_lev_key}"
                break
        except (TypeError, ValueError):
            continue

    if _explicit is None:
        orch = mkt.get("orchestrator", {}) if isinstance(mkt, dict) else {}
        if isinstance(orch, dict):
            for _lev_key in ("leverage", "final_leverage", "recommended_leverage"):
                try:
                    _raw_lev = orch.get(_lev_key)
                    if _raw_lev is not None and float(_raw_lev) > 0:
                        _explicit = float(_raw_lev)
                        _source = f"orchestrator:{_lev_key}"
                        break
                except (TypeError, ValueError):
                    continue

    if _explicit is not None:
        dyn_leverage = int(round(_explicit))
    else:
        try:
            from backend.services.dynamic_leverage_calculator import calculate_dynamic_leverage
            dyn_leverage = int(round(calculate_dynamic_leverage(db, sym, account_id)))
            _source = "dynamic_calc"
        except Exception:
            dyn_leverage = 8  # V5: 默认 15→8，最终由 SizingAgent 波动率连续映射裁定
            _source = "default"

    dyn_leverage = max(
        int(DYNAMIC_LEVERAGE_MIN),
        min(int(DYNAMIC_LEVERAGE_MAX), dyn_leverage),
    )

    try:
        from backend.services.risk_band_resolver import (
            stage_e_active, resolve_leverage, LeverageCapContext, get_correlation_bucket,
        )
        if stage_e_active() and sym:
            _bucket = get_correlation_bucket(sym)
            _bucket_open = 0
            if _bucket and isinstance(market_summary, dict):
                _open_by_sym = market_summary.get("_open_positions_by_symbol", {}) or {}
                for _s in (_bucket.get("symbols", []) if _bucket else []):
                    if _open_by_sym.get(_s):
                        _bucket_open += 1
            _tier_norm = (tier or "mid").strip().lower()
            _capped, _reason = resolve_leverage(
                sym,
                LeverageCapContext(
                    ai_override=float(dyn_leverage),
                    nature=trade_nature or None,
                    count_same_bucket_open=_bucket_open,
                    tier=_tier_norm,
                ),
            )
            if _capped < dyn_leverage:
                logger.info(
                    f"[FullAuto][StageE] {sym}[{_tier_norm}] leverage "
                    f"{dyn_leverage}x → {_capped}x ({_reason})"
                )
            dyn_leverage = max(1, int(_capped))
    except Exception as _e_lev:
        logger.warning(f"[FullAuto][StageE] leverage cap 异常: {_e_lev}")

    try:
        _risk_cap = dec.get("leverage_cap")
        if _risk_cap is not None:
            _risk_cap = int(_risk_cap)
            if 0 < _risk_cap < dyn_leverage:
                dyn_leverage = _risk_cap
                _source = f"{_source}+trade_risk_cap"
    except (TypeError, ValueError):
        pass

    # 权威最终钳制(阶段 C §3): leverage_authority 是 last word,
    # 所有主路径杠杆在返回前过一次权威的 tier cap(权威源唯一)。
    # 不改变内部计算逻辑,仅确保权威表(long=12 / short=mid=20)是最终约束。
    try:
        from backend.services.leverage_authority import resolve_leverage as _auth_lev
        _final_lev = _auth_lev(tier=tier, requested=float(dyn_leverage))
        # 权威可能返回浮点(如 floor 到 1.0);保持 int 语义以兼容历史返回类型。
        dyn_leverage = max(1, int(round(_final_lev)))
    except Exception as _e_auth_lev:
        logger.warning(f"[FullAuto][LeverageAuth] authority clamp 异常: {_e_auth_lev}")

    return dyn_leverage, _source

def resolve_decision_position_pct(
    dec: dict,
    confidence: int,
    vol_value: float,
    open_position_count: int,
    tier: str,
    tier_budget_pct: float,
    total_equity: float,
    market_regime: str,
    sym: str,
    action: str,
    host: DecisionSizingHost,
) -> tuple:
    _ai_pct = host.extract_ai_position_pct(dec)
    if _ai_pct is not None:
        _tdi_meta = {
            "base_pct": _ai_pct,
            "final_pct": _ai_pct,
            "position_source": "ai_strategy",
        }
        logger.info(
            f"[FullAuto] {sym} {action} 使用AI策略仓位 {_ai_pct:.1%}"
        )
        return _ai_pct, _tdi_meta

    _base_pct = ai_dynamic_position_pct(
        confidence, vol_value, open_position_count,
        tier=tier, tier_budget_pct=tier_budget_pct,
    )
    return apply_tdi_position_advice(
        symbol=sym,
        base_pct=_base_pct,
        confidence=confidence,
        volatility=vol_value,
        open_position_count=open_position_count,
        tier=tier,
        tier_budget_pct=tier_budget_pct,
        equity=float(total_equity or 0.0),
        regime=market_regime if isinstance(market_regime, str) else "ranging",
        base_direction=("long" if action == "buy" else ("short" if action == "sell" else "hold")),
    )

def calibrate_confidence(
    raw_conf: int, action: str, symbol: str,
    analyst_reports: dict, market_summary: dict,
    host: DecisionSizingHost,
) -> int:
    if action == "hold":
        return raw_conf

    # ── 以下计算仅用于日志标注，不改变返回值 ──
    adj = 0
    mkt = (market_summary or {}).get(symbol, {})
    _orch = mkt.get("orchestrator", {}) if isinstance(mkt, dict) else {}
    _final_side = (_orch.get("final_side") or "").lower() if isinstance(_orch, dict) else ""
    _ai_side = "long" if action in ("buy", "pyramid") else (
        "short" if action in ("sell",) else "")
    _dir_opposed = (
        _ai_side
        and _final_side in ("long", "short")
        and _final_side != _ai_side
        and float(_orch.get("weighted_confidence", 0) or 0) >= 0.10
    )

    if raw_conf == 50:
        adj -= 2

    _prescreen_passed = getattr(host, 'pre_screen_passed', set())
    if (
        _prescreen_passed
        and symbol.upper() in {s.upper() for s in _prescreen_passed}
        and not _dir_opposed
    ):
        adj += 5

    bull_count, bear_count = 0, 0
    for _name, _report in (analyst_reports or {}).items():
        if not _report:
            continue
        r = _report if isinstance(_report, dict) else (
            _report.to_dict() if hasattr(_report, 'to_dict') else {})
        for sig in r.get("signals", []):
            sig_sym = sig.get("symbol", "")
            if sig_sym and sig_sym.upper() != symbol.upper():
                continue
            s = sig.get("signal", "")
            if s == "bullish":
                bull_count += 1
            elif s in ("bearish", "danger", "warning"):
                bear_count += 1

    is_buy = action in ("buy", "pyramid")
    is_sell = action in ("sell",)
    is_directional = is_buy or is_sell
    if is_directional:
        signal_support = bull_count if is_buy else bear_count
        signal_oppose = bear_count if is_buy else bull_count
        if signal_oppose > signal_support and signal_oppose >= 2:
            adj -= min(20, (signal_oppose - signal_support) * 5)
        elif signal_support > signal_oppose + 1 and signal_support >= 3:
            adj += min(10, (signal_support - signal_oppose) * 3)

    mkt = (market_summary or {}).get(symbol, {})
    trend = mkt.get("trend_direction", "neutral")
    if is_directional:
        if is_buy and trend == "bearish":
            adj -= 10
        elif is_sell and trend == "bullish":
            adj -= 10
        elif (is_buy and trend == "bullish") or (is_sell and trend == "bearish"):
            adj += 5
    if _dir_opposed:
        adj -= 10

    vol_regime = mkt.get("volatility_regime", "normal")
    if vol_regime == "extreme":
        adj -= 10
    elif vol_regime == "high":
        adj -= 5

    # AI 主驾：返回原始置信度，不改写。校准差异仅记日志供溯源。
    _would_calibrate = max(10, min(95, raw_conf + adj))
    if _would_calibrate != raw_conf:
        logger.info(
            f"[FullAuto] 置信度溯源(未改写) {symbol} {action}: AI={raw_conf}, "
            f"规则校准建议={_would_calibrate}(adj={adj:+d}, 多{bull_count}/空{bear_count}, "
            f"trend={trend}, vol={vol_regime}) — 保留AI原始值"
        )
    return raw_conf
