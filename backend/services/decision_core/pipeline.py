"""decision_core 对外门面。

执行层与提示词层各一个入口，避免调用方关心内部模块划分。
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def evaluate_open_decision(
    *,
    db,
    account_id: int,
    symbol: str,
    dec: dict,
    market_data: Optional[dict] = None,
    base_entry_threshold: int = 50,
    is_auto_coin: bool = False,
    mode: str = "paper",
) -> Tuple[bool, str, Dict]:
    """执行层开仓前调用。返回 (allowed, reason, adjustments)。"""
    from backend.services.decision_core.unified_gate import evaluate_entry

    tier = (dec.get("timeframe_tier") or dec.get("tier") or "mid").lower()
    nature = (dec.get("trade_nature") or "swing").lower()
    action = (dec.get("action") or dec.get("operation") or "").lower()

    # ── 整改#11：LLM 数值防幻觉校验（用快照纠正 LLM 引用的 RSI/价格等）──
    if isinstance(market_data, dict):
        try:
            import os as _os
            _reason = dec.get("reasoning") or dec.get("analysis") or dec.get("thesis_summary") or ""
            if _reason and _os.getenv("MARKET_DATA_VERIFIER_ENABLED", "false").strip().lower() in (
                "1", "true", "yes", "on",
            ):
                from backend.services.ai.market_data_verifier import MarketDataVerifier
                _vr = MarketDataVerifier().verify(str(_reason), market_data)
                if not _vr.verified and _vr.corrected_values:
                    market_data = dict(market_data)
                    market_data.update(_vr.corrected_values)
                    market_data["_verifier_corrected"] = True
                    logger.info("[V5Gate][Verifier#11] %s 纠正 %d 项 LLM 数值幻觉",
                                symbol, len(_vr.discrepancies))
        except Exception as _ver_err:
            logger.debug("[V5Gate][Verifier#11] 跳过: %s", _ver_err)

    # Strict Data Contract（Live/Paper 一致，Paper 可 WARN）
    if action in ("buy", "sell", "pyramid", "dca"):
        from backend.services.decision_core.data_contract import apply_data_contract_gate
        dc_ok, dc_reason = apply_data_contract_gate(tier, market_data, mode=mode)
        if not dc_ok:
            return False, dc_reason, {}

    # DCP 方向一致性（在置信度/费用门控之前）
    if action in ("buy", "sell", "pyramid", "dca"):
        from backend.services.decision_core.direction_coherence import (
            evaluate_direction_coherence,
        )

        orch = {}
        if isinstance(market_data, dict):
            orch = market_data.get("orchestrator") or {}
        dcp = evaluate_direction_coherence(
            action=action,
            confidence=_safe_float(dec.get("confidence")) or 0.0,
            tier=tier,
            trade_nature=nature,
            orchestrator=orch,
            fan_branch=dec.get("_fan_branch") or "",
            symbol=symbol,
            trading_mode=mode,
        )
        if not dcp.allowed:
            return False, f"[DCP] {dcp.rule}: {dcp.reason}", {}
        if dcp.penalty > 0:
            base_entry_threshold = int(base_entry_threshold) + dcp.penalty

    tp_pct = _safe_float(dec.get("take_profit_pct"))
    sl_pct = _safe_float(dec.get("stop_loss_pct"))
    # Fix 3: 从 market_data 提取 ATR 百分比，用于自适应 TP/SL
    _atr_pct = 0.0
    if isinstance(market_data, dict):
        # Fix 10: 兼容多种 ATR 字段名（market_summary 用 atr_value，因子引擎用 atr）
        _atr_val = _safe_float(
            market_data.get("atr")
            or market_data.get("atr_value")
            or market_data.get("atr_14")
        )
        _price_val = _safe_float(market_data.get("price") or market_data.get("current_price"))
        if (_atr_val or 0) > 0 and (_price_val or 0) > 0:
            _atr_pct = _atr_val / _price_val
        # 也支持直接传 atr_pct
        if _atr_pct <= 0:
            _atr_pct = _safe_float(market_data.get("atr_pct"))
    tier_defaults = _tier_tp_sl_defaults(tier, atr_pct=_atr_pct)
    # 震荡均值回归模式（2026-07-09）：MR 单的小止盈止损（0.6%~1.2%）会被
    # _looks_like_ai_placeholder_tp_sl 误判成"AI 占位符"而替换成 tier 大默认值，
    # 从而抹掉 MR 打法。ranging_mr 单跳过占位符替换与 tier 兜底，原样沿用传入 tp/sl。
    _mr_flag = bool(isinstance(market_data, dict) and market_data.get("ranging_mr"))
    # Master LLM 常输出 2%/1% 占位符；实际执行走 ATR band，门控应使用 tier 真实默认
    if not _mr_flag and tp_pct and sl_pct and _looks_like_ai_placeholder_tp_sl(tp_pct, sl_pct):
        logger.debug(
            "[V5Gate] %s tier=%s AI TP/SL 占位 (%s/%s) → 门控改用 tier 默认",
            symbol, tier, f"{tp_pct:.1%}", f"{sl_pct:.1%}",
        )
        tp_pct = _safe_float(tier_defaults.get("tp_pct")) or tp_pct
        sl_pct = _safe_float(tier_defaults.get("sl_pct")) or sl_pct
    if not tp_pct or not sl_pct:
        tp_pct = tp_pct or _safe_float(tier_defaults.get("tp_pct"))
        sl_pct = sl_pct or _safe_float(tier_defaults.get("sl_pct"))

    _env = dec.get("_agent_envelope") if isinstance(dec.get("_agent_envelope"), dict) else {}
    _mlto = dec.get("_mlto_thesis") if isinstance(dec.get("_mlto_thesis"), dict) else {}
    _thesis_id = str(_env.get("thesis_id") or _mlto.get("thesis_id") or "")
    _readiness = _env.get("open_readiness")
    if _readiness is None:
        _readiness = _mlto.get("open_readiness")
    _hub_adj = _env.get("hub_adjusted")
    if _hub_adj is None:
        _hub_adj = _mlto.get("hub_adjusted")
    _hub_comp = _env.get("hub_composite")
    if _hub_comp is None:
        _hub_comp = _mlto.get("hub_composite")

    result = evaluate_entry(
        db=db,
        account_id=account_id,
        symbol=symbol,
        action=dec.get("action") or dec.get("operation") or "",
        confidence=_safe_float(dec.get("confidence")) or 0.0,
        tier=tier,
        trade_nature=nature,
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        market_data=market_data,
        base_entry_threshold=base_entry_threshold,
        is_auto_coin=is_auto_coin,
        mode=mode,
        thesis_id=_thesis_id,
        open_readiness=int(_readiness) if _readiness is not None else None,
        hub_composite=float(_hub_comp) if _hub_comp is not None else None,
        hub_adjusted=float(_hub_adj) if _hub_adj is not None else None,
    )
    adjustments = dict(result.adjustments or {})

    # 编排器 soft 模式：wait/frozen 缩仓不 block（仅当该 mode 下硬门控未启用时）。
    # 2026-07-06 整改：是否硬门控按 trading_mode 区分（Live 强制 true），
    # 不能再用进程级单一 ORCHESTRATOR_HARD_GATE 常量判断。
    if result.allowed and action in ("buy", "sell"):
        try:
            from backend.config.settings import (
                ORCHESTRATOR_WAIT_OVERRIDE_CONF,
                get_orchestrator_hard_gate,
            )
            if not get_orchestrator_hard_gate(mode) and isinstance(market_data, dict):
                orch = market_data.get("orchestrator") or {}
                if isinstance(orch, dict):
                    orch_act = (
                        orch.get("final_action") or orch.get("action") or ""
                    ).lower()
                    sm = float(adjustments.get("size_multiplier") or 1.0)
                    from backend.services.decision_core.threshold_resolver import normalize_confidence_pct
                    conf = normalize_confidence_pct(_safe_float(dec.get("confidence")) or 0.0)
                    if orch_act == "frozen":
                        sm *= 0.35
                    elif orch_act == "wait" and conf < float(ORCHESTRATOR_WAIT_OVERRIDE_CONF or 75):
                        sm *= 0.5
                    if sm < 0.999:
                        adjustments["size_multiplier"] = round(sm, 3)
        except Exception as err:
            logger.debug("[MidLongGate] orch soft 跳过: %s", err)

    return result.allowed, result.reason, adjustments


def evaluate_midlong_open(
    *,
    db,
    account_id: int,
    symbol: str,
    dec: dict,
    market_data: Optional[dict] = None,
    mode: str = "paper",
    persistence_allow: bool = True,
) -> Tuple[bool, str, Dict]:
    """中线/长线组合门控（设计 Phase1）：DCP + V5 + Regime + 编排器 soft，一次 evaluate。

    不在此函数外再叠 Persistence / MLTO open_gate / 一致性门控。
    """
    action = (dec.get("action") or "").lower()
    if action not in ("buy", "sell"):
        return False, "not_entry", {}

    if not persistence_allow:
        try:
            from backend.config.settings import MIDLONG_PERSISTENCE_TICKS
            ticks = max(1, int(MIDLONG_PERSISTENCE_TICKS or 1))
        except Exception:
            ticks = 1
        return False, f"[Persistence] 需连续{ticks}tick同向", {}

    # ── P1-1 修复：中长线 nature 归一 ──
    # swing/position/mid/long → trend_follow（中长线一体），与 unified_gate 门内归一一致，
    # 消除「swing 用 40 门槛 vs trend_follow 用 resolve_trend_min_score」的分叉。
    nature = (dec.get("trade_nature") or "swing").lower()
    tier = (dec.get("timeframe_tier") or dec.get("tier") or "mid").lower()
    from backend.services.decision_core.unified_gate import normalize_v5_nature
    norm_nature = normalize_v5_nature(nature)
    if norm_nature == "nature_ambiguous":
        return False, f"[MidLong] nature_ambiguous trade_nature={nature!r} 无法归一 → hold", {}
    nature = norm_nature
    base = 50
    if nature == "trend_follow":
        try:
            from backend.services.trend_agent import resolve_trend_min_score
            base = resolve_trend_min_score(mode)
        except Exception:
            base = 50

    if isinstance(market_data, dict) and "mtf_resonance" not in market_data:
        try:
            from backend.services.decision_core.mtf_resonance import inject_mtf_into_market_summary
            inject_mtf_into_market_summary(market_data)
        except Exception:
            pass

    # 长线并发持仓上限（修复：原 LongWeeklyCap 统计历史开仓订单导致一周 6 笔封顶，
    # 即使仓位已平仍计数。改为统计当前 open 持仓数。）
    if nature in ("trend_follow", "position") and db is not None and account_id:
        try:
            from backend.config.settings import TREND_MAX_CONCURRENT_LONG
            from backend.database.models import PaperPosition
            _open_count = int(db.query(PaperPosition).filter(
                PaperPosition.account_id == int(account_id),
                PaperPosition.status == "open",
                PaperPosition.trade_nature.in_(("trend_follow", "position")),
            ).count())
            _cap = int(TREND_MAX_CONCURRENT_LONG or 10)
            if _open_count >= _cap:
                return False, (
                    f"[LongConcurrentCap] 长线当前 {_open_count} 个持仓 ≥ 上限 {_cap}"
                ), {}
        except Exception as err:
            logger.debug("[MidLongGate] concurrent cap 跳过: %s", err)

    allowed, reason, adjustments = evaluate_open_decision(
        db=db,
        account_id=account_id,
        symbol=symbol,
        dec=dec,
        market_data=market_data,
        base_entry_threshold=base,
        mode=mode,
    )

    # Paper Agent 独立路径：Agent 已 should_open → 置信度达 base 则缩仓放行（block→scale，非新 gate）
    if (
        not allowed
        and dec.get("_agent_independent")
        and (mode or "").lower() == "paper"
    ):
        from backend.services.decision_core.threshold_resolver import normalize_confidence_pct
        _conf = normalize_confidence_pct(dec.get("confidence_pct") or dec.get("confidence") or 0)
        _rule = (reason or "").lower()
        if _conf >= base and ("confidence" in _rule or "有效门槛" in (reason or "")):
            _sm = max(0.30, min(0.85, 0.35 + (_conf - base) * 0.015))
            adjustments = dict(adjustments or {})
            adjustments["size_multiplier"] = float(
                adjustments.get("size_multiplier") or 1.0
            ) * _sm
            allowed = True
            reason = (
                f"[PaperAgentProbe] conf={_conf:.0f}≥base={base} 缩仓放行 "
                f"size×{adjustments['size_multiplier']:.2f} (原拦截:{reason[:80]})"
            )
            logger.info("[MidLongGate] %s %s %s", symbol, action, reason)

    # Monte Carlo 轻量 tail 预检 → 缩仓不 block
    if allowed and action in ("buy", "sell"):
        try:
            from backend.config.settings import MIDLONG_MONTE_CARLO_ENABLED
            if MIDLONG_MONTE_CARLO_ENABLED:
                from backend.services.decision_core.monte_carlo_gate import estimate_tail_risk
                _sl = _safe_float(dec.get("stop_loss_pct")) or 0.04
                mc = estimate_tail_risk(
                    market_data=market_data if isinstance(market_data, dict) else {},
                    sl_pct=_sl,
                    side=action,
                )
                sm = float(adjustments.get("size_multiplier") or 1.0)
                if mc.size_multiplier < sm:
                    adjustments["size_multiplier"] = mc.size_multiplier
                    adjustments["monte_carlo"] = mc.detail
                    logger.info(
                        "[MonteCarlo] %s %s tail=%.2f%% → size×%.2f",
                        symbol, action, mc.tail_loss_pct * 100, mc.size_multiplier,
                    )
        except Exception as err:
            logger.debug("[MonteCarlo] 跳过: %s", err)

    # ── S1-2 期望值(EV)闸门：扣往返成本后期望为正才放行 ──
    # 放在最后：仅当前置全部放行时才算 EV，避免无谓计算；影子模式只记录不拦截。
    if allowed and action in ("buy", "sell"):
        try:
            from backend.services.decision_core.midlong_ev_gate import midlong_ev_gate
            _ev = midlong_ev_gate.evaluate(
                nature=nature,
                symbol=symbol,
                score=float(dec.get("confidence") or 0),
                direction="long" if action == "buy" else "short",
                tp_pct=_safe_float(dec.get("take_profit_pct")) or 0.0,
                sl_pct=_safe_float(dec.get("stop_loss_pct")) or 0.0,
                exchange=(dec.get("exchange") or None),
            )
            adjustments["ev_gate"] = {
                "ev_pct": _ev.ev_pct,
                "p_win": _ev.p_win,
                "ev_min": _ev.ev_min,
                "p_win_source": _ev.p_win_source,
            }
            if not _ev.allowed:
                logger.info("[MidLongEvGate] BLOCK %s %s %s", symbol, action, _ev.reason)
                return False, f"[EVGate] {_ev.reason}", adjustments
        except Exception as err:
            logger.debug("[MidLongEvGate] 跳过: %s", err)

    return allowed, reason, adjustments


def build_v5_prompt_block(
    *,
    db=None,
    account_id: Optional[int] = None,
    market_data: Optional[dict] = None,
) -> str:
    """Master / Direction / Risk 共用的 V5 纪律提示块（费用教育 + 市场状态）。"""
    parts = []

    try:
        from backend.config.settings import (
            V5_DECISION_CORE_ENABLED,
            V5_MIN_RISK_REWARD,
        )

        if not V5_DECISION_CORE_ENABLED:
            return ""

        if db is not None and account_id is not None:
            from backend.services.decision_core.fee_context import build_fee_context

            fee_ctx = build_fee_context(db, account_id, daily_cap=0)
            # 2026-08-05 移除：不再展示日开仓配额（各周期独立已废弃）。
            parts.append(fee_ctx.prompt_block(show_trade_cap=False))

        if market_data is not None:
            from backend.services.decision_core.regime_agent import classify_regime

            regime = classify_regime(market_data)
            parts.append(f"## 🌡️ 市场状态\n- {regime.prompt_hint()}")

        parts.append(
            "## 📐 盈亏结构铁律\n"
            f"- 开仓必须满足 TP:SL ≥ {V5_MIN_RISK_REWARD}:1，否则系统硬拦截\n"
            "- 亏小赚大是唯一可持续模式：止损要近（按计划执行），止盈要远（让利润奔跑）\n"
            "- 赚 0.3% 就跑、亏 9% 才砍的行为模式 = 给交易所打工"
        )
    except Exception as err:
        logger.debug("[V5Prompt] 构建跳过: %s", err)

    return "\n\n".join(p for p in parts if p)


def _safe_float(value) -> Optional[float]:
    try:
        f = float(value)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _tier_tp_sl_defaults(tier: str, atr_pct: float = 0.0) -> dict:
    """获取 tier 的 TP/SL 默认值。

    Fix 3: 当 tier 配置 use_atr=True 且提供了 atr_pct 时，
    返回基于 ATR 自适应的 TP/SL，而非固定值。
    高波动币(atr_pct大) → 更宽止损；低波动币 → 更紧止损。
    """
    try:
        from backend.config.settings import TIER_TP_SL_DEFAULTS
        cfg = TIER_TP_SL_DEFAULTS.get(tier, {})
        if not cfg:
            return {}

        # 不启用 ATR 自适应，或 ATR 不可用 → 返回固定值
        if not cfg.get("use_atr") or atr_pct <= 0:
            return {"tp_pct": cfg.get("tp_pct", 0), "sl_pct": cfg.get("sl_pct", 0)}

        # ATR 自适应：SL = clamp(atr_sl_mult × atr_pct, min, max)
        sl_pct = max(
            cfg.get("min_sl_pct", 0.015),
            min(cfg.get("max_sl_pct", 0.04), cfg.get("atr_sl_mult", 1.5) * atr_pct),
        )
        tp_pct = max(
            cfg.get("min_tp_pct", 0.025),
            min(cfg.get("max_tp_pct", 0.06), cfg.get("atr_tp_mult", 2.25) * atr_pct),
        )
        return {"tp_pct": tp_pct, "sl_pct": sl_pct, "atr_based": True}
    except Exception:
        return {}


def _looks_like_ai_placeholder_tp_sl(tp_pct: float, sl_pct: float) -> bool:
    """识别 Master 输出的泛化占位 TP/SL（非执行层 ATR band 结果）。"""
    if tp_pct <= 0.03 and sl_pct <= 0.015:
        return True
    rr = tp_pct / sl_pct if sl_pct > 0 else 0
    return rr > 0 and rr <= 2.05 and tp_pct <= 0.10
