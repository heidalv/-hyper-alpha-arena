"""中长线独立 Agent 辅助 — 从 monolith 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def build_midlong_health_from_facts(lookback_days: int = 14, account_id: Optional[int] = None) -> Dict[str, Any]:
    """中线/长线健康视图（[2026-08-17] 替代已删除的 midlong_health_report）。

    直接从 trade_facts（真实交易事件流）按 tier 汇总：笔数/胜率/净 PnL。
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import text as _sa_text

    from backend.database.connection import SessionLocal

    since = datetime.now(timezone.utc) - timedelta(days=int(lookback_days))
    out: Dict[str, Any] = {
        "lookback_days": int(lookback_days),
        "source": "trade_facts",
        "tiers": {},
        "totals": {"trades": 0, "wins": 0, "pnl": 0.0},
    }
    try:
        with SessionLocal() as db:
            rows = db.execute(_sa_text(
                """
                SELECT tier, COUNT(*) AS n,
                       SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) AS wins,
                       SUM(COALESCE(pnl,0)) AS pnl
                FROM trade_facts
                WHERE ts >= :since
                  AND (:acct IS NULL OR account_id = :acct)
                GROUP BY tier
                ORDER BY n DESC
                """
            ), {"since": since, "acct": account_id}).mappings().all()
        for r in rows:
            n = int(r["n"] or 0)
            w = int(r["wins"] or 0)
            out["tiers"][str(r["tier"])] = {
                "trades": n,
                "win_rate": round(w / n, 4) if n else 0.0,
                "pnl": round(float(r["pnl"] or 0.0), 4),
            }
            out["totals"]["trades"] += n
            out["totals"]["wins"] += w
            out["totals"]["pnl"] = round(out["totals"]["pnl"] + float(r["pnl"] or 0.0), 4)
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:200]
    return out


def _clamp_tp_to_tier_max(tp_pct, tier, symbol, action):
    """[P0-1] TP 上限 clamp：复用 mid_long_structure_stop 的 MLTO_*_MAX_TP（long 20% / mid 10%）。

    防止 LLM 拍脑袋设 20%+ 目标（实测 id=2641 TP=+22.4%），全库 peak 上限仅 5.03%，
    超出 tier 上限的 TP 永远触达不了。
    """
    try:
        _key = "LONG" if str(tier or "").lower() == "long" else "MID"
        _max = float(os.getenv(f"MLTO_{_key}_MAX_TP", "0.20" if _key == "LONG" else "0.10"))
        _tp = float(tp_pct or 0)
        if _tp > _max:
            logger.info(
                "[MidLongTPClamp] %s %s tier=%s: tp %.1f%% → %.1f%% (max)",
                symbol, action, tier, _tp * 100, _max * 100,
            )
            return _max
        return _tp
    except Exception as _e:
        logger.debug("[MidLongTPClamp] %s 跳过: %s", symbol, _e)
        return tp_pct


@dataclass
class MidlongHelpersHost:
    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    evaluate_and_execute_proposal: Callable = field(repr=False, default=lambda *a, **k: False)


def build_midlong_helpers_host(svc) -> MidlongHelpersHost:
    return MidlongHelpersHost(
        get_trading_account_id=svc._get_trading_account_id,
        append_event=svc._append_event,
        evaluate_and_execute_proposal=svc._evaluate_and_execute_proposal,
    )


def resolve_independent_strategy(
    db: Session, session, sym_u: str, tier: str, host: MidlongHelpersHost,
):
    from backend.database.models import AIStrategy as _AIStrategy

    sym_u = str(sym_u).upper()
    tier = (tier or "mid").lower()
    active_ids = list(getattr(session, "active_strategy_ids", None) or [])
    if active_ids:
        strat = (
            db.query(_AIStrategy)
            .filter(
                _AIStrategy.strategy_id.in_(active_ids),
                _AIStrategy.primary_symbol == sym_u,
                _AIStrategy.timeframe_tier == tier,
                _AIStrategy.status == "active",
            )
            .first()
        )
        if strat:
            return strat

    account_id = host.get_trading_account_id(db, session)
    if account_id:
        strat = (
            db.query(_AIStrategy)
            .filter(
                _AIStrategy.account_id == account_id,
                _AIStrategy.primary_symbol == sym_u,
                _AIStrategy.timeframe_tier == tier,
                _AIStrategy.status == "active",
            )
            .order_by(_AIStrategy.updated_at.desc())
            .first()
        )
        if strat:
            return strat

    # Paper：策略可能落在历史 account_id 上，开单仍走 session.paper_account_id
    _trade_mode = (getattr(session, "trading_mode", "") or "paper").strip().lower()
    if _trade_mode == "paper":
        strat = (
            db.query(_AIStrategy)
            .filter(
                _AIStrategy.primary_symbol == sym_u,
                _AIStrategy.timeframe_tier == tier,
                _AIStrategy.status == "active",
            )
            .order_by(_AIStrategy.updated_at.desc())
            .first()
        )
        if strat:
            logger.info(
                "[Agent独立] %s tier=%s Paper 跨账户策略 %s (strat_acct=%s session_acct=%s)",
                sym_u, tier, (strat.strategy_id or "")[:16],
                getattr(strat, "account_id", "?"), account_id,
            )
            if strat.strategy_id not in active_ids:
                try:
                    active_ids.append(strat.strategy_id)
                    session.active_strategy_ids = active_ids
                except Exception:
                    pass
            return strat
    return None

def try_execute_independent_agent_open(
    *,
    db: Session,
    session,
    sym: str,
    tier: str,
    action: str,
    confidence: int,
    sl_pct: float = 0.0,
    tp_pct: float = 0.0,
    trade_nature: str,
    market_summary: dict,
    session_mode: str = "running",
    host: MidlongHelpersHost,
    # S2-5 新增：LLM 的 exit_plan（tp_sl_proposal）传入，写入持仓 exit_state_json
    tp_sl_proposal: Optional[Dict] = None,
    invalidation_condition: str = "",
    expected_hold_hours: float = 0.0,
    # [Phase D 修复 Bug1] tranche_gate.compute_margin_pct 计算出的分档保证金比例。
    # < 1.0 时按此比例缩放最终下单 size（乘在 budget/V5Gate/MTF 缩仓之后）。
    # 默认 1.0 = 不缩（向后兼容：未传则保持原行为，整仓下单）。
    tranche_margin_pct: float = 1.0,
    # v6 M6：方向一致性审计（可选）
    thesis_dir: str = "",
    hub_dir: str = "",
    hub_mode: str = "",
    dir_src: str = "",
    authority: str = "",
) -> bool:
    from backend.services.decision_core.proposal import TradeProposal

    _sym_u = str(sym).upper()
    _act = (action or "hold").lower()
    _session_id_aud = str(getattr(session, "session_id", "") or "")

    def _audit_skip(_reason: str, *, stage: str = "exec") -> None:
        try:
            from backend.services.mlto.midlong_direction_audit import (
                record_decision_audit,
            )
            record_decision_audit(
                outcome="skip",
                stage=stage,
                symbol=_sym_u,
                reason=str(_reason or "exec_block")[:160],
                session_id=_session_id_aud,
                tier=str(tier or ""),
                source="exec",
                authority=str(authority or ""),
                action=_act,
                direction=str(hub_dir or ""),
                score=int(confidence or 0),
                mode=str(hub_mode or ""),
            )
        except Exception:
            pass

    # === 固定交易对守卫（阶段0 Task1）：auto-coin 符号绝不能触发长线开仓 ===
    # 长线下单唯一终点（短线 paper_engine.place_order 不经此函数）。
    # tier=long 或 trade_nature ∈ (trend_follow, position) 时，
    # 符号不在 get_fixed_symbols_for_session 正向白名单 → 拒绝开仓。
    # 阶段0 暂不拦截 mid/swing（mid 路径仍在运行），仅守 long/trend/position。
    _tier_l = (tier or "").strip().lower()
    _tn_l = (trade_nature or "").strip().lower()
    # P1：归一保留 swing；禁止把 mid 强制改写成 long（否则 AI 中线撞 FixedSymbolGate）
    try:
        from backend.services.full_auto.midlong_executor import (
            is_midlong_nature,
            normalize_midlong_nature,
        )
        if is_midlong_nature(_tn_l) or _tier_l in ("mid", "long"):
            trade_nature = normalize_midlong_nature(_tn_l, _tier_l)
            _tn_l = trade_nature
            if _tier_l == "mid" or _tn_l == "swing":
                tier = "mid"
                _tier_l = "mid"
            elif _tn_l in ("trend_follow", "position"):
                tier = "long"
                _tier_l = "long"
    except Exception as _norm_err:
        logger.debug("[MidLong] nature 归一跳过: %s", _norm_err)
    # 固定币守卫仅拦长线；中线 AI 选币允许非白名单
    if _tier_l == "long" or _tn_l in ("trend_follow", "position"):
        try:
            from backend.services.auto_coin_selector import get_fixed_symbols_for_session
            _session_id = getattr(session, "session_id", None)
            _fixed = get_fixed_symbols_for_session(_session_id, db, tier="long") if _session_id else set()
            if _fixed and _sym_u not in _fixed:
                logger.warning(
                    "[FixedSymbolGate] auto-coin/非固定符号 %s 在 %s 长线开仓门被拦截 "
                    "(session=%s, tier=%s, trade_nature=%s)",
                    _sym_u, _tier_l or "long", _session_id, _tier_l, _tn_l,
                )
                host.append_event(
                    session, "fixed_symbol_gate_block",
                    f"[固定币守卫] {_sym_u} tier={_tier_l} trade_nature={_tn_l} "
                    f"不在长线白名单，已拒绝开仓",
                )
                _audit_skip(f"fixed_symbol_gate:{_sym_u}")
                return False
        except Exception as _gate_err:
            # 守卫本身异常不应阻断开仓（容错优先）；记录后继续。
            logger.debug("[FixedSymbolGate] %s 守卫检查异常跳过: %s", _sym_u, _gate_err)

    # 长线开仓前确保周线指标已注入（防御性兜底）。
    # [2026-07-31] 与 MLTO 分析层统一：本币周线缺失则 fail-closed，禁止借 BTC/ETH。
    if tier == "long" and isinstance(market_summary, dict):
        inject_midlong_indicators(market_summary, _sym_u, include_weekly=True)
        _ms = market_summary.get(_sym_u) or {}
        if not _ms.get("indicators_1w"):
            logger.warning(
                "[MidLongExec] %s 本币周线缺失，拒绝长线开仓（fail-closed，不借大盘）",
                _sym_u,
            )
            host.append_event(
                session, "midlong_1w_missing",
                f"[长线1w] {_sym_u} 本币周线缺失，已拒绝开仓",
            )
            _audit_skip("midlong_1w_missing")
            return False

    # 开仓执行前确保 DB 连接健康（防止上游 MLTO LLM 长时间占连接导致事务损坏）
    try:
        from sqlalchemy import text as _sa_text
        db.execute(_sa_text("SELECT 1"))
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass

    # ── S1-3 入场多周期一致性约束：逆更高周期强偏向 → 否决/缩仓 ──
    _mtf_size_mult = 1.0
    try:
        from backend.services.decision_core.midlong_mtf_constraint import (
            evaluate_midlong_mtf_constraint,
        )
        _mtf = evaluate_midlong_mtf_constraint(
            symbol=_sym_u,
            tier=(tier or "mid").lower(),
            direction=_act,
            market_data=(market_summary or {}).get(_sym_u) if isinstance(market_summary, dict) else None,
        )
        if _mtf.veto:
            logger.info("[MidLongMTF] BLOCK %s %s: %s", _sym_u, _act, _mtf.reason)
            host.append_event(
                session, "midlong_mtf_block",
                f"[中长线MTF] {_sym_u} {_act} 否决: {_mtf.reason[:120]}",
            )
            _audit_skip(f"midlong_mtf_block:{_mtf.reason}")
            return False
        _mtf_size_mult = float(_mtf.size_multiplier or 1.0)
    except Exception as _mtf_err:
        logger.debug("[MidLongMTF] %s 约束跳过: %s", _sym_u, _mtf_err)

    # ── S0-1 止血修复（R1）：独立路径接入 reentry_cooldown ──
    # 背景：Master 路径开仓前调用 reentry_cooldown.reopen_blocked()，但独立路径
    # （try_execute_independent_agent_open）此前从未调用——同币种同方向平仓后
    # 立即再开，是"开仓→亏损→同向再开→继续亏损"恶性循环的直接代码层根因。
    # 复用现有 reentry_cooldown 模块（tier 隔离 + 连亏倍率 + close_reason 感知），
    # 而非新建独立冷却模块（04 综合方案的"复用现有代码"原则）。
    # Flag: MIDLONG_INDEPENDENT_COOLDOWN_ENFORCE（默认 true，影子模式可关）。
    try:
        from backend.config.settings import MIDLONG_INDEPENDENT_COOLDOWN_ENFORCE
        _cd_enforce = bool(MIDLONG_INDEPENDENT_COOLDOWN_ENFORCE)
    except Exception:
        _cd_enforce = True
    if _cd_enforce and _act in ("buy", "sell"):
        try:
            from backend.services.reentry_cooldown import reopen_blocked
            _account_id_cd = host.get_trading_account_id(db, session)
            if _account_id_cd:
                _tier_cd = (tier or "mid").strip().lower()
                if _tier_cd not in ("short", "mid", "long"):
                    _tier_cd = "mid"
                _blocked, _cd_reason = reopen_blocked(
                    _account_id_cd, _sym_u, _act, _tier_cd,
                )
                if _blocked:
                    logger.info(
                        "[MidLongCooldown] BLOCK %s %s tier=%s: %s",
                        _sym_u, _act, _tier_cd, _cd_reason,
                    )
                    host.append_event(
                        session, "midlong_cooldown_block",
                        f"[中长线冷却] {_sym_u} {_act} tier={_tier_cd}: {_cd_reason}",
                    )
                    # 即使被拦截也持久化决策日志（R8 修复：account_id 兜底落库）
                    try:
                        persist_independent_scan_log(
                            account_id=_account_id_cd,
                            symbol=_sym_u, tier=tier, trade_nature=trade_nature,
                            action="hold", confidence=0,
                            reasoning=f"[冷却拦截] {_cd_reason}",
                            agent_source=f"{trade_nature}_cooldown_blocked",
                            market_summary=market_summary,
                        )
                    except Exception:
                        pass
                    _audit_skip(f"midlong_cooldown_block:{_cd_reason}")
                    return False
                # ── P0-E 分层熔断：周期级日亏预算（只冻本 tier，绝不跨周期）──
                try:
                    from backend.services.tier_circuit_breaker import (
                        is_tier_open_blocked as _tier_cb_blocked,
                    )
                    _tier_blk, _tier_why = _tier_cb_blocked(_account_id_cd, _tier_cd)
                    if _tier_blk:
                        logger.info(
                            "[TierCircuit] BLOCK %s %s tier=%s: %s",
                            _sym_u, _act, _tier_cd, _tier_why,
                        )
                        host.append_event(
                            session, "tier_circuit_block",
                            f"⛔ 周期熔断[{_tier_cd}] {_sym_u} {_act}: {_tier_why[:100]}",
                        )
                        _audit_skip(f"tier_circuit_block:{_tier_why[:60]}")
                        return False
                except Exception as _tier_cb_err:
                    logger.debug("[TierCircuit] 检查跳过: %s", _tier_cb_err)
        except Exception as _cd_err:
            logger.debug("[MidLongCooldown] %s 冷却检查跳过: %s", _sym_u, _cd_err)

    # ── v6 M3：LLM exit_plan 止损直通；禁止 max(LLM, structure) 加宽 ──
    # 有 LLM sl → 用之；structure 仅 LLM 缺失时兜底；随后仅 ATR×1.5 地板抬升。
    _sl_source = "llm" if float(sl_pct or 0) > 0 else ""
    _structure_sl_pct = 0.0
    _structure_tp_pct = 0.0
    try:
        from backend.config.settings import MIDLONG_STRUCTURE_STOP_ON_INDEPENDENT
        _use_struct_sl = bool(MIDLONG_STRUCTURE_STOP_ON_INDEPENDENT)
    except Exception:
        _use_struct_sl = True
    if _use_struct_sl and _act in ("buy", "sell") and float(sl_pct or 0) <= 0:
        try:
            from backend.services.mid_long_structure_stop import mid_long_structure_stop
            _ms_for_stop = (market_summary or {}).get(_sym_u) if isinstance(market_summary, dict) else None
            if not isinstance(_ms_for_stop, dict):
                _ms_for_stop = {}
            _ref_price = float(
                _ms_for_stop.get("current_price")
                or _ms_for_stop.get("price")
                or _ms_for_stop.get("mark_price")
                or 0.0
            )
            if _ref_price <= 0:
                try:
                    _klines = _ms_for_stop.get("klines")
                    if _klines is not None and hasattr(_klines, "iloc"):
                        _ref_price = float(_klines.iloc[-1]["close"])
                except Exception:
                    pass
            if _ref_price > 0:
                _agent_src = "trend_agent" if (tier or "").lower() in ("long",) else "swing_agent"
                _sl_p, _tp_p, _sl_price, _tp_price, _sl_src = mid_long_structure_stop.compute(
                    symbol=_sym_u,
                    market_data=_ms_for_stop,
                    side=_act,
                    entry=_ref_price,
                    agent_source=_agent_src,
                )
                if _sl_p > 0:
                    _structure_sl_pct = float(_sl_p)
                    sl_pct = _structure_sl_pct
                    _sl_source = "structure_fallback"
                    logger.info(
                        "[MidLongStructureSL] %s %s tier=%s: LLM 缺失 → structure sl=%.2f%% (fallback)",
                        _sym_u, _act, tier, _structure_sl_pct * 100,
                    )
                if _tp_p > 0 and float(tp_pct or 0) <= 0:
                    _structure_tp_pct = float(_tp_p)
                    tp_pct = _structure_tp_pct
        except Exception as _ss_err:
            logger.debug("[MidLongStructureSL] %s 结构 SL 跳过: %s", _sym_u, _ss_err)

    # [P0-1] LLM 给的 tp_pct 上限 clamp（mid 10% / long 20%）：防 LLM 拍脑袋设 20%+ 目标
    if float(tp_pct or 0) > 0:
        tp_pct = _clamp_tp_to_tier_max(tp_pct, tier, _sym_u, _act)

    # ── P1：ATR 止损地板 + funding 净 RR + ATR 仓位；chop 仅缩仓不否决 ──
    _atr_size_mult = 1.0
    if (tier or "").lower() == "long" and _act in ("buy", "sell"):
        try:
            from backend.services.mlto.midlong_trade_design import (
                apply_structure_atr_floor,
                atr_size_multiplier,
                estimate_atr_1d_pct,
                funding_net_rr_ok,
                is_chop_regime,
            )
            _ms_td = (market_summary or {}).get(_sym_u) if isinstance(market_summary, dict) else {}
            if not isinstance(_ms_td, dict):
                _ms_td = {}
            _orch_td = (_ms_td.get("orchestrator") if isinstance(_ms_td.get("orchestrator"), dict) else {}) or {}
            _chop, _chop_why = is_chop_regime(_ms_td, _orch_td)
            if _chop:
                # v6：chop 不得否决 AI 方向——最多缩仓 + soft_warning
                _atr_size_mult *= 0.5
                logger.info(
                    "[MidLongChop] SOFT %s %s size×0.5: %s",
                    _sym_u, _act, _chop_why,
                )
                host.append_event(
                    session, "midlong_chop_soft",
                    f"[震荡缩仓] {_sym_u}: {_chop_why}",
                )
            _atr = estimate_atr_1d_pct(_ms_td)
            if _atr is not None and not _ms_td.get("atr_1d_pct"):
                _ms_td["atr_1d_pct"] = _atr
                if isinstance(market_summary, dict):
                    market_summary.setdefault(_sym_u, _ms_td)
                    market_summary[_sym_u]["atr_1d_pct"] = _atr
            _sl_before_floor = float(sl_pct or 0)
            sl_pct, _atr_floor_why = apply_structure_atr_floor(
                sl_pct=_sl_before_floor, atr_1d_pct=_atr,
            )
            if "→" in str(_atr_floor_why):
                logger.info("[MidLongATR] %s %s", _sym_u, _atr_floor_why)
                if _sl_source.startswith("llm") or _sl_source == "llm":
                    _sl_source = "llm+atr_floor"
                elif not _sl_source:
                    _sl_source = "atr_floor"
            elif not _sl_source and float(sl_pct or 0) > 0:
                _sl_source = "llm"
            # TP 至少满足净 RR（粗：2×SL）；若原 TP 更宽则保留
            if float(tp_pct or 0) < float(sl_pct or 0) * 2.0:
                tp_pct = float(sl_pct or 0) * 2.0
            # [P0-1] RR 地板可能把 TP 抬过 tier 上限 → 再 clamp 回 max（20%/10%）
            tp_pct = _clamp_tp_to_tier_max(tp_pct, tier, _sym_u, _act)
            _fr_ok, _nrr, _fr_why = funding_net_rr_ok(
                action=_act,
                tp_pct=float(tp_pct or 0),
                sl_pct=float(sl_pct or 0),
                funding_rate=_ms_td.get("funding_rate"),
            )
            if not _fr_ok:
                logger.info("[MidLongFunding] BLOCK %s %s: %s", _sym_u, _act, _fr_why)
                host.append_event(session, "midlong_funding_block", f"[费率RR] {_sym_u}: {_fr_why}")
                _audit_skip(f"midlong_funding_block:{_fr_why}")
                return False
            _atr_sz, _atr_sz_why = atr_size_multiplier(
                sl_pct=float(sl_pct or 0), atr_1d_pct=_atr,
            )
            _atr_size_mult *= float(_atr_sz or 1.0)
            if _atr_size_mult < 0.999:
                logger.info("[MidLongATRSize] %s %s", _sym_u, _atr_sz_why)
        except Exception as _td_err:
            logger.debug("[MidLongTradeDesign] %s 跳过: %s", _sym_u, _td_err)

    # [Phase D 修复 Bug1] 把 tranche 分档保证金比例夹紧到 [0,1]，传给 proposal。
    # proposal_execution 会把它作为 size 乘子叠加到 budget/V5Gate/MTF 之后。
    try:
        _tranche_mult = float(tranche_margin_pct)
        if _tranche_mult != _tranche_mult or _tranche_mult < 0:  # NaN 或负数 → 不缩
            _tranche_mult = 1.0
        if _tranche_mult > 1.0:
            _tranche_mult = 1.0
    except (TypeError, ValueError):
        _tranche_mult = 1.0
    # ATR 仓位乘在 tranche 之上（只缩不放）
    try:
        _tranche_mult = max(0.0, min(1.0, float(_tranche_mult) * float(_atr_size_mult or 1.0)))
    except Exception:
        pass

    # ── P2：净方向敞口 + 相关簇同向上限（在仓位乘子确定后估名义）──
    if _act in ("buy", "sell") and (
        (tier or "").lower() in ("mid", "long")
        or _tn_l in ("swing", "trend_follow", "position")
    ):
        try:
            from backend.services.mlto.midlong_portfolio_risk import (
                check_portfolio_open_allowed,
                estimate_open_notional,
            )
            from backend.services.paper_trading_engine import paper_engine

            _acct_pf = host.get_trading_account_id(db, session)
            _portfolio = None
            _est_notional = 0.0
            _equity = 0.0
            _pos_list = None
            if _acct_pf:
                _bal = paper_engine.get_balance(db, _acct_pf) or {}
                _pos_list = paper_engine.get_positions(db, _acct_pf, status="open") or []
                _equity = float(
                    (_bal or {}).get("total_equity")
                    or (_bal or {}).get("equity")
                    or (_bal or {}).get("balance")
                    or 0
                )
                _portfolio = {"balance": _bal, "positions": _pos_list}
                try:
                    from backend.config import settings as _cfg_pf
                    _risk_pct = float(getattr(_cfg_pf, "MIDLONG_RISK_PCT", 0.01) or 0.01)
                except Exception:
                    _risk_pct = 0.01
                # 杠杆：跟真实成交对齐（同币已有仓跟仓；否则默认 10x）
                _lev = 10.0
                try:
                    from backend.services.leverage_authority import (
                        DEFAULT_LEVERAGE,
                        extract_existing_symbol_leverage,
                        resolve_leverage,
                    )
                    _exist_lev = extract_existing_symbol_leverage(_sym_u, _pos_list)
                    if _exist_lev and float(_exist_lev) > 0:
                        _lev = float(_exist_lev)
                    else:
                        _lev = float(
                            resolve_leverage(
                                tier=(tier or "mid").lower(),
                                requested=float(DEFAULT_LEVERAGE),
                            )
                            or DEFAULT_LEVERAGE
                        )
                except Exception:
                    _lev = 10.0
                # 名义 = 权益 × 保证金比例 × 杠杆（与 ETH 成交口径一致）
                _est_notional = estimate_open_notional(
                    equity=_equity,
                    margin_frac=float(_tranche_mult or 0),
                    leverage=_lev,
                    sl_pct=float(sl_pct or 0),
                    risk_pct=_risk_pct,
                )
            _is_probe = str(dir_src or "").startswith("nibble_probe")
            _pf_ok, _pf_why = check_portfolio_open_allowed(
                symbol=_sym_u,
                action=_act,
                portfolio=_portfolio,
                new_notional=_est_notional,
                is_probe=_is_probe,
            )
            if not _pf_ok:
                logger.info(
                    "[MidLongPortfolio] BLOCK %s %s: %s (est_notional=%.1f equity=%.1f margin×=%.3f)",
                    _sym_u, _act, _pf_why, _est_notional, _equity, float(_tranche_mult or 0),
                )
                host.append_event(
                    session, "midlong_portfolio_block",
                    f"[组合风控] {_sym_u} {_act}: {_pf_why}",
                )
                _audit_skip(f"midlong_portfolio_block:{_pf_why}")
                return False
        except Exception as _pf_err:
            logger.debug("[MidLongPortfolio] %s 跳过: %s", _sym_u, _pf_err)

    # ── 组合级风险预算（v6 计划 阶段1 第4项，下单前最后一道检查）──
    # 组合日 VaR / 单币集中度 / 策略 3σ 熔断 / 冻结信号。持仓/收益序列模块内
    # TTL 缓存；paper fail-open、live fail-closed。
    if _act in ("buy", "sell"):
        try:
            from backend.services.risk_management.portfolio_budget import (
                portfolio_budget as _pb,
            )
            _pb_strategy = (
                "midlong"
                if (tier or "").lower() in ("mid", "long")
                or _tn_l in ("swing", "trend_follow", "position")
                else str(trade_nature or "midlong").lower()
            )
            _pb_mode = (getattr(session, "trading_mode", "") or "paper").strip().lower()
            _pb_dec = _pb.evaluate_open(
                symbol=_sym_u,
                action=_act,
                notional_usd=float(_est_notional or 0),
                equity=float(_equity or 0),
                strategy=_pb_strategy,
                mode=_pb_mode,
                db=db,
                account_id=int(_acct_pf or 0),
                positions=_pos_list if "_pos_list" in locals() else None,
            )
            if not _pb_dec.allowed:
                logger.info(
                    "[MidLongPortfolio] BLOCK %s %s: portfolio_budget %s",
                    _sym_u, _act, ";".join(_pb_dec.reasons[:3]),
                )
                host.append_event(
                    session, "portfolio_budget_block",
                    f"[组合预算] {_sym_u} {_act}: {';'.join(_pb_dec.reasons[:3])}",
                )
                _audit_skip(
                    "portfolio_budget_block:" + ";".join(_pb_dec.reasons[:3])
                )
                return False
        except Exception as _pb_err:
            logger.debug("[MidLongPortfolio] %s 组合预算跳过: %s", _sym_u, _pb_err)

    _extra_kwargs = {
        "mtf_size_mult": _mtf_size_mult,
        "tp_sl_proposal": tp_sl_proposal or None,
        "invalidation_condition": invalidation_condition or "",
        "expected_hold_hours": float(expected_hold_hours or 0),
        "tranche_margin_pct": _tranche_mult,
    }

    if not _sl_source and float(sl_pct or 0) > 0:
        _sl_source = "llm"
    logger.info(
        "[MidLong] stage=open_ready symbol=%s action=%s sl_source=%s sl=%.2f%% tp=%.2f%%",
        _sym_u, _act, _sl_source or "-", float(sl_pct or 0) * 100, float(tp_pct or 0) * 100,
    )

    proposal = TradeProposal.from_agent(
        sym=_sym_u,
        tier=(tier or "mid").lower(),
        action=_act,
        confidence=int(confidence or 0),
        trade_nature=trade_nature,
        sl_pct=float(sl_pct or 0),
        tp_pct=float(tp_pct or 0),
        source_lane=f"{trade_nature}_independent",
        **_extra_kwargs,
    )
    _eval = getattr(host, "evaluate_and_execute_proposal", None)
    if not callable(_eval):
        logger.error(
            "[MidLong] host missing evaluate_and_execute_proposal (type=%s) symbol=%s",
            type(host).__name__, _sym_u,
        )
        try:
            from backend.services.mlto.midlong_direction_audit import record_decision_audit
            record_decision_audit(
                outcome="skip",
                stage="exec",
                symbol=_sym_u,
                reason="host_missing_evaluate_and_execute_proposal",
                session_id=str(getattr(session, "session_id", "") or ""),
                tier=(tier or "").lower(),
                action=_act,
                mode=hub_mode or "",
                direction=hub_dir or "",
                authority=authority or "",
                extra={"host_type": type(host).__name__},
            )
        except Exception:
            pass
        return False

    try:
        _ok = bool(_eval(
            db=db,
            session=session,
            proposal=proposal,
            market_summary=market_summary,
            session_mode=session_mode,
        ))
    except Exception as _eval_err:
        logger.warning(
            "[MidLong] evaluate_and_execute_proposal failed %s: %s",
            _sym_u, _eval_err,
        )
        try:
            from backend.services.mlto.midlong_direction_audit import record_decision_audit
            record_decision_audit(
                outcome="skip",
                stage="exec",
                symbol=_sym_u,
                reason=("exec_exception:%s:%s" % (type(_eval_err).__name__, _eval_err))[:160],
                session_id=str(getattr(session, "session_id", "") or ""),
                tier=(tier or "").lower(),
                action=_act,
                mode=hub_mode or "",
                direction=hub_dir or "",
                authority=authority or "",
            )
        except Exception:
            pass
        return False

    if _ok and _act in ("buy", "sell"):
        try:
            from backend.services.mlto.midlong_direction_audit import record_open_audit
            from backend.services.mlto.decision_hub import ai_governed_enabled
            _mode = hub_mode or ("ai_governed" if ai_governed_enabled() else "standard")
            record_open_audit(
                symbol=_sym_u,
                fill_dir=_act,
                thesis_dir=thesis_dir,
                hub_dir=hub_dir,
                sl_source=_sl_source or "",
                mode=_mode,
                dir_src=dir_src,
                authority=authority,
                session_id=str(getattr(session, "session_id", "") or ""),
            )
        except Exception as _aud_err:
            logger.debug("[MidLongAudit] open record skip: %s", _aud_err)
    elif not _ok and _act in ("buy", "sell"):
        try:
            from backend.services.mlto.midlong_direction_audit import record_decision_audit
            record_decision_audit(
                outcome="skip",
                stage="exec",
                symbol=_sym_u,
                reason="evaluate_and_execute_returned_false",
                session_id=str(getattr(session, "session_id", "") or ""),
                tier=(tier or "").lower(),
                action=_act,
                mode=hub_mode or "",
                direction=hub_dir or "",
                authority=authority or "",
            )
        except Exception:
            pass
    return _ok

def record_midlong_factor_snapshots(
    *,
    db,
    account_id: int,
    trade_id: int,
    symbol: str,
    side: str,
    market_data: dict,
) -> None:
    try:
        mf = (market_data or {}).get("midlong_factors") if isinstance(market_data, dict) else None
        if not isinstance(mf, dict):
            return
        from backend.database.models import SignalTradeFeedback
        _dir = "long" if (side or "").lower() == "buy" else "short"
        n = 0
        for tf in ("4h", "1d"):
            vals = mf.get(tf) or {}
            if not isinstance(vals, dict):
                continue
            for fid, v in vals.items():
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                if fv != fv:  # NaN
                    continue
                db.add(SignalTradeFeedback(
                    account_id=account_id,
                    trade_id=trade_id,
                    symbol=(symbol or "").upper()[:20],
                    signal_type=f"factor:{fid}"[:50],
                    signal_value=fv,
                    signal_direction=_dir[:20],
                    trade_side=(side or "")[:10],
                ))
                n += 1
        if n:
            db.commit()
    except Exception as e:
        logger.debug("[MidLongFactorIC] %s 因子快照记录跳过: %s", symbol, e)
        try:
            db.rollback()
        except Exception:
            pass

def persist_independent_scan_log(
    *,
    account_id: Optional[int],
    symbol: str,
    tier: str,
    trade_nature: str,
    action: str,
    confidence: float,
    reasoning: str,
    agent_source: str,
    cited_fact_ids: Optional[List[str]] = None,
    evidence_audit: Optional[dict] = None,
    market_summary: Optional[dict] = None,
    llm_tp_sl_proposal: Optional[dict] = None,
    lifecycle: Optional[str] = None,
    scenarios: Optional[dict] = None,
    invalidation: Optional[dict] = None,
) -> None:
    # ── S1-12 修复（R5）：account_id 为空时不再静默 return ──
    # 原逻辑：if not account_id: return
    # 后果：ai_decision_logs 14 天 0 条记录 → 无法做 conf 校准、无法做 prompt A/B、
    #      无法回溯审计 → confidence_calibrator 永远停在 cold_linear →
    #      midlong_ev_gate 冷启动豁免永久生效 → 低 EV 交易被放行。
    # 修复：account_id 为空时改用 0 兜底（专用 audit account），确保 LLM 决策被持久化。
    if not account_id:
        logger.warning(
            "[ScanLog] %s %s account_id 为空，仍尝试落库（account_id=0）", symbol, tier,
        )
        account_id = 0
    try:
        from backend.database.connection import AnalyticsSessionLocal
        from backend.database.models import AIDecisionLog
        from decimal import Decimal as _Decimal

        _mkt_orch = {}
        if isinstance(market_summary, dict):
            _sym_data = market_summary.get(symbol) or {}
            if isinstance(_sym_data, dict):
                _mkt_orch = _sym_data.get("orchestrator") or {}
        if not isinstance(_mkt_orch, dict):
            _mkt_orch = {}

        _ana_db = AnalyticsSessionLocal()
        try:
            entry = AIDecisionLog(
                account_id=int(account_id),
                reason=(reasoning or f"[{agent_source}] {action}")[:1000],
                operation=(action or "hold").lower(),
                symbol=str(symbol).upper(),
                prev_portion=_Decimal("0"),
                target_portion=_Decimal("0"),
                total_balance=_Decimal("0"),
                executed="false",
                reasoning_snapshot=(reasoning or "")[:4000] or None,
                decision_source=agent_source or "llm",
                decision_snapshot=json.dumps({
                    "trade_nature": trade_nature,
                    "tier": tier,
                    "confidence": confidence,
                    "reasoning": (reasoning or "")[:2000],
                    "agent_source": agent_source,
                    "cited_fact_ids": list(cited_fact_ids or []),
                    **({"agent_evidence": evidence_audit} if evidence_audit else {}),
                    # S1-12 新增：v3 schema 字段持久化（对应 04 综合方案 §2.3.4）
                    **({"llm_tp_sl_proposal": llm_tp_sl_proposal} if llm_tp_sl_proposal else {}),
                    **({"lifecycle": lifecycle} if lifecycle else {}),
                    **({"scenarios": scenarios} if scenarios else {}),
                    **({"invalidation": invalidation} if invalidation else {}),
                    "_scan_log": True,
                }, ensure_ascii=False),
                short_bias=str(_mkt_orch.get("short_bias") or "") or None,
                short_confidence=float(_mkt_orch.get("short_confidence") or 0) or None,
                mid_bias=str(_mkt_orch.get("mid_bias") or "") or None,
                mid_confidence=float(_mkt_orch.get("mid_confidence") or 0) or None,
                long_bias=str(_mkt_orch.get("long_bias") or "") or None,
                long_confidence=float(_mkt_orch.get("long_confidence") or 0) or None,
            )
            _ana_db.add(entry)
            _ana_db.commit()
        finally:
            _ana_db.close()
    except Exception as _log_err:
        logger.debug("[ScanLog] %s %s 审计落库跳过: %s", symbol, tier, _log_err)

def _compute_midlong_indicator_block(kdf, period: Optional[str] = None) -> dict:
    """从 OHLCV DataFrame 计算长线 quant brief / MLTO 所需指标。

    [2026-07-31] 补齐 macd_hist / adx / trend：此前只写 RSI/EMA，导致
    MidLongQuantBrief 永久 missing macd_hist_1h/adx_1d/trend_1w，alignment≤7/15，
    LLM 长期 neutral + recommend_open=False，中长线开不出仓。
    """
    _ind: dict = {}
    if kdf is None or getattr(kdf, "empty", True) or "close" not in kdf.columns:
        return _ind
    _delta = kdf["close"].diff()
    _gain = _delta.where(_delta > 0, 0.0)
    _loss = (-_delta).where(_delta < 0, 0.0)
    _avg_g = _gain.ewm(alpha=1 / 14, adjust=False).mean()
    _avg_l = _loss.ewm(alpha=1 / 14, adjust=False).mean()
    _rs = _avg_g / _avg_l.replace(0, 1e-10)
    _ind["rsi"] = round(float((100 - 100 / (1 + _rs)).iloc[-1]), 1)
    _ema9 = kdf["close"].ewm(span=9, adjust=False).mean().iloc[-1]
    _ema21 = kdf["close"].ewm(span=21, adjust=False).mean().iloc[-1]
    _ema50 = kdf["close"].ewm(span=50, adjust=False).mean().iloc[-1] if len(kdf) >= 50 else _ema21
    _ind["ema9"] = round(float(_ema9), 2)
    _ind["ema21"] = round(float(_ema21), 2)
    _ind["ema50"] = round(float(_ema50), 2)
    _ind["ema_trend"] = (
        "bullish" if _ema9 > _ema21 > _ema50
        else "bearish" if _ema9 < _ema21 < _ema50 else "mixed"
    )
    _ind["trend"] = _ind["ema_trend"]
    # MACD histogram（12/26/9）
    try:
        _ema12 = kdf["close"].ewm(span=12, adjust=False).mean()
        _ema26 = kdf["close"].ewm(span=26, adjust=False).mean()
        _macd = _ema12 - _ema26
        _signal = _macd.ewm(span=9, adjust=False).mean()
        _hist = _macd - _signal
        _ind["macd"] = round(float(_macd.iloc[-1]), 6)
        _ind["macd_signal"] = round(float(_signal.iloc[-1]), 6)
        _ind["macd_hist"] = round(float(_hist.iloc[-1]), 6)
    except Exception:
        pass
    # ADX(14) 简化版
    try:
        if all(c in kdf.columns for c in ("high", "low", "close")) and len(kdf) >= 20:
            _up = kdf["high"].diff()
            _down = -kdf["low"].diff()
            _plus_dm = _up.where((_up > _down) & (_up > 0), 0.0)
            _minus_dm = _down.where((_down > _up) & (_down > 0), 0.0)
            _tr = (kdf["high"] - kdf["low"]).combine(
                (kdf["high"] - kdf["close"].shift()).abs(), max
            ).combine(
                (kdf["low"] - kdf["close"].shift()).abs(), max
            )
            _atr = _tr.ewm(alpha=1 / 14, adjust=False).mean()
            _plus_di = 100 * (_plus_dm.ewm(alpha=1 / 14, adjust=False).mean() / _atr.replace(0, 1e-10))
            _minus_di = 100 * (_minus_dm.ewm(alpha=1 / 14, adjust=False).mean() / _atr.replace(0, 1e-10))
            _dx = 100 * (_plus_di - _minus_di).abs() / (_plus_di + _minus_di).replace(0, 1e-10)
            _adx = _dx.ewm(alpha=1 / 14, adjust=False).mean()
            _ind["adx"] = round(float(_adx.iloc[-1]), 1)
            _ind["plus_di"] = round(float(_plus_di.iloc[-1]), 1)
            _ind["minus_di"] = round(float(_minus_di.iloc[-1]), 1)
            # [P1-补强] 输出 ATR（绝对值），供 midlong_helpers 的 volatility_pct 兜底
            # 补算（atr_1d_pct 缺失时用 atr/current_price）。
            _ind["atr"] = round(float(_atr.iloc[-1]), 6)
    except Exception:
        pass
    if "volume" in kdf.columns and len(kdf) >= 20:
        _vol = kdf["volume"]
        _period_sec_map = {
            "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
            "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800,
            "12h": 43200, "1d": 86400, "1w": 604800, "1M": 2592000,
        }
        _period_sec = _period_sec_map.get(period, 0) if period else 0
        _last_ts = (
            int(kdf["timestamp"].iloc[-1])
            if "timestamp" in kdf.columns and kdf["timestamp"].iloc[-1] is not None
            else 0
        )
        _partial = bool(
            _period_sec > 0 and _last_ts > 0
            and int(time.time()) < _last_ts + _period_sec
        )
        if _partial and len(kdf) >= 2:
            _cur_vol = float(_vol.iloc[-2])
            _vol_ma = float(_vol.iloc[-21:-1].mean())
        else:
            _cur_vol = float(_vol.iloc[-1])
            _vol_ma = float(_vol.iloc[-20:].mean())
        _ind["vol_ratio"] = (
            round(_cur_vol / _vol_ma, 2)
            if _vol_ma > 0 and _cur_vol > 0
            else None
        )
    # 最近30根 OHLCV（带时间）喂提示词 / evidence / MLTO brief
    _cols = [c for c in ("datetime", "timestamp", "open", "high", "low", "close", "volume") if c in kdf.columns]
    _recent = kdf.tail(30)[_cols].round(4) if _cols else kdf.tail(30)
    _ind["recent_klines"] = _recent.to_dict("records")
    return _ind


def _indicator_block_incomplete(ind: dict | None) -> bool:
    """已有 indicators_* 但缺 quant brief 关键字段时，需要重算。"""
    if not isinstance(ind, dict) or not ind:
        return True
    return any(ind.get(k) is None for k in ("rsi", "ema_trend", "macd_hist", "adx", "trend"))


def _inject_structure_levels(ms: dict) -> None:
    """从 4h K线最近 8 根摆动高低点补算支撑/阻力，写入 ms['structure_levels']。

    仅供 quant_brief 展示与 LLM 关键位参考；数据不足或结构位不跨现价两侧时
    保持缺失（下游输出诚实的“无明确支撑/阻力数据”文案），绝不虚构数值。
    """
    if not isinstance(ms, dict) or ms.get("structure_levels"):
        return
    _ind4 = ms.get("indicators_4h")
    if not isinstance(_ind4, dict):
        return
    rows = _ind4.get("recent_klines")
    if not isinstance(rows, list) or len(rows) < 8:
        return
    highs: list = []
    lows: list = []
    for row in rows[-8:]:
        if not isinstance(row, dict):
            continue
        try:
            h = float(row.get("high") or 0)
            l = float(row.get("low") or 0)
        except (TypeError, ValueError):
            continue
        if h > 0:
            highs.append(h)
        if l > 0:
            lows.append(l)
    if len(highs) < 3 or len(lows) < 3:
        return
    price = float(ms.get("current_price") or ms.get("price") or 0)
    support = min(lows)
    resistance = max(highs)
    if price > 0 and (support >= price or resistance <= price):
        return
    ms["structure_levels"] = {"support": support, "resistance": resistance}


def inject_midlong_indicators(
    market_summary: dict, symbol: str, include_weekly: bool = False,
) -> None:
    sym = str(symbol).upper()
    if not isinstance(market_summary, dict):
        return
    # 注意：空 dict {} 在 Python 里是 falsy，不能用 `get() or {}`，否则会丢掉
    # market_summary 里已有的引用，指标写到游离 dict、调用方永远看不到。
    ms = market_summary.get(sym)
    if ms is None and symbol != sym:
        ms = market_summary.get(symbol)
    if not isinstance(ms, dict):
        ms = {}
    market_summary[sym] = ms
    try:
        from backend.services.decision_core.regime_agent import classify_regime
        _reg = classify_regime(ms)
        _fresh_regime = {
            "name": _reg.regime,
            "size_multiplier": getattr(_reg, "size_multiplier", 1.0),
            "detail": getattr(_reg, "detail", "") or "",
        }
        _existing_regime = ms.get("regime")
        if isinstance(_existing_regime, dict):
            # 陈旧/占位 name（unknown/空）用新分类愈合；已有有效 name 保留
            if not _existing_regime.get("name") or str(
                _existing_regime.get("name")
            ).strip().lower() in ("", "unknown"):
                _existing_regime["name"] = _fresh_regime["name"]
            _existing_regime.setdefault(
                "size_multiplier", _fresh_regime["size_multiplier"]
            )
            _existing_regime.setdefault("detail", _fresh_regime["detail"])
        else:
            ms["regime"] = _fresh_regime
    except Exception:
        pass
    # S4 基座：把中长线活跃因子在 4h/1d 的读数注入 market_data，供 Swing/Trend 参考。
    try:
        from backend.config.settings import MIDLONG_FACTOR_RESEARCH_ENABLED
        if MIDLONG_FACTOR_RESEARCH_ENABLED and "midlong_factors" not in ms:
            from backend.services.factor_engine.midlong_active_factor_set import (
                midlong_active_factor_set,
            )
            _snap = midlong_active_factor_set.build_snapshot(sym)
            if _snap.get("count"):
                ms["midlong_factors"] = _snap
    except Exception:
        pass
    # 长线(include_weekly)把 1w 也纳入"是否需要补K线"的判断，否则 1h/4h/1d 齐了
    # 就提前 return、周线永远补不上 → 长线继续被 StrictData 卡死。
    # [2026-08-10 v3.1.0] 中线（非 weekly）额外纳入 15m：供入场择时验证，
    # qual_layer 的 K 线摘要段优先读 indicators_15m.recent_klines。
    _tfs = ("1h", "4h", "1d", "1w") if include_weekly else ("15m", "1h", "4h", "1d")
    # 缺整块 或 缺 macd/adx/trend → 都要重算（不能因“有个空壳 indicators_1h”就提前 return）
    _need_klines = any(_indicator_block_incomplete(ms.get(f"indicators_{_tf}")) for _tf in _tfs)
    if not _need_klines:
        # 仍同步 trend_1w 别名，供 quant brief 读取
        _iw = ms.get("indicators_1w") if isinstance(ms.get("indicators_1w"), dict) else {}
        if _iw.get("trend") or _iw.get("ema_trend"):
            ms["trend_1w"] = _iw.get("trend") or _iw.get("ema_trend")
        if ms.get("indicators_1d") and isinstance(ms["indicators_1d"], dict):
            _adx = ms["indicators_1d"].get("adx")
            if _adx is not None:
                ms["adx_1d"] = _adx
        try:
            from backend.services.decision_core.mtf_resonance import inject_mtf_into_market_summary
            inject_mtf_into_market_summary(ms)
        except Exception:
            pass
        try:
            from backend.services.orchestrator_derivatives import inject_derivatives_into_market_summary
            inject_derivatives_into_market_summary(market_summary, sym)
        except Exception:
            pass
        _inject_structure_levels(ms)
        return
    try:
        import pandas as _kp
        from backend.services.kline_data_service import kline_service as _ks
        for _tf in _tfs:
            if not _indicator_block_incomplete(ms.get(f"indicators_{_tf}")):
                continue
            # 周线数据天然稀少，最小根数放宽到 8（对齐主循环 :8776）；其余周期仍要 20 根。
            _min_bars = 8 if _tf == "1w" else 20
            # 决策热路径：只走 data_center(purpose=trade)，禁止 get_kline_data 旁路/过期兜底
            _raw = _ks.get_aggregated_klines(sym, _tf, count=60)
            if not _raw or len(_raw) < _min_bars:
                logger.info(
                    "[MidLong] %s/%s K线不足(%s<%s)，跳过该周期（不跨所/不借大盘）",
                    sym, _tf, len(_raw or []), _min_bars,
                )
                continue
            _kdf = _kp.DataFrame(_raw)
            if "datetime" not in _kdf.columns and "timestamp" in _kdf.columns:
                _kdf["datetime"] = _kp.to_datetime(_kdf["timestamp"], unit="s", utc=True).astype(str)
            ms[f"indicators_{_tf}"] = _compute_midlong_indicator_block(
                _kdf, period=_tf
            )
            # [P1-修复] 从 1h K线补算 price_change_1h/24h_pct 与 volatility_pct：
            # 独立循环 merge 后这三个字段可能缺失，导致 classify_regime 恒判 ranging。
            # 用与 unified_data_pool 一致的 1h 口径（close[-1]/close[-2]、close[-1]/close[-25]）。
            if _tf == "1h" and len(_kdf) >= 2:
                _c = _kdf["close"].astype(float)
                _p_last = float(_c.iloc[-1])
                if _p_last > 0:
                    if ms.get("price_change_1h_pct") is None and float(_c.iloc[-2]) > 0:
                        ms["price_change_1h_pct"] = round(
                            (_p_last / float(_c.iloc[-2]) - 1.0) * 100.0, 4,
                        )
                    if ms.get("price_change_24h_pct") is None and len(_c) >= 25 and float(_c.iloc[-25]) > 0:
                        ms["price_change_24h_pct"] = round(
                            (_p_last / float(_c.iloc[-25]) - 1.0) * 100.0, 4,
                        )
        # 补 volatility_pct（口径与 classify_regime 期望一致：ATR/price 小数 0.01~0.05）
        if ms.get("volatility_pct") is None:
            _v = ms.get("atr_1d_pct") or 0
            if float(_v or 0) > 0:
                ms["volatility_pct"] = float(_v)
            else:
                _id1 = ms.get("indicators_1d") if isinstance(ms.get("indicators_1d"), dict) else {}
                _atr = _id1.get("atr")
                _px = ms.get("current_price") or 0
                if _atr and float(_px or 0) > 0:
                    ms["volatility_pct"] = float(_atr) / float(_px)
        # 顶层别名：quant brief 读 md.trend_1w / md.adx_1d
        _iw = ms.get("indicators_1w") if isinstance(ms.get("indicators_1w"), dict) else {}
        if _iw.get("trend") or _iw.get("ema_trend"):
            ms["trend_1w"] = _iw.get("trend") or _iw.get("ema_trend")
        _id = ms.get("indicators_1d") if isinstance(ms.get("indicators_1d"), dict) else {}
        if _id.get("adx") is not None:
            ms["adx_1d"] = _id.get("adx")
        from backend.services.decision_core.mtf_resonance import inject_mtf_into_market_summary
        inject_mtf_into_market_summary(ms)
    except Exception as err:
        logger.debug("[MidLong] 指标注入 %s 跳过: %s", sym, err)
    try:
        from backend.services.orchestrator_derivatives import inject_derivatives_into_market_summary
        inject_derivatives_into_market_summary(market_summary, sym)
    except Exception:
        pass
    _inject_structure_levels(ms)
