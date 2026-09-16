"""Proposal 评估与执行 — 从 monolith _evaluate_and_execute_proposal 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Tuple

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class ProposalExecutionHost:
    midlong_persistence_allow: Callable = field(repr=False, default=lambda *a, **k: True)
    resolve_independent_strategy: Callable = field(repr=False, default=lambda *a, **k: None)
    session_trading_mode: Callable = field(repr=False, default=lambda *a, **k: "paper")
    persist_tcp_snapshot: Callable = field(repr=False, default=lambda *a, **k: None)
    build_portfolio_for_agents: Callable = field(repr=False, default=lambda *a, **k: {})
    decision_price_consistency_ok: Callable = field(repr=False, default=lambda *a, **k: (True, ""))
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    live_constitutional_pre_trade_check: Callable = field(repr=False, default=lambda *a, **k: (True, ""))
    execute_live_trade: Callable = field(repr=False, default=lambda *a, **k: None)
    safe_commit: Callable = field(repr=False, default=lambda *a, **k: True)
    execute_paper_trade: Callable = field(repr=False, default=lambda *a, **k: False)
    record_midlong_factor_snapshots: Callable = field(repr=False, default=lambda *a, **k: None)


def build_proposal_execution_host(svc) -> ProposalExecutionHost:
    return ProposalExecutionHost(
        midlong_persistence_allow=svc._midlong_persistence_allow,
        resolve_independent_strategy=svc._resolve_independent_strategy,
        session_trading_mode=svc._session_trading_mode,
        persist_tcp_snapshot=svc._persist_tcp_snapshot,
        build_portfolio_for_agents=svc._build_portfolio_for_agents,
        decision_price_consistency_ok=svc._decision_price_consistency_ok,
        append_event=svc._append_event,
        live_constitutional_pre_trade_check=svc._live_constitutional_pre_trade_check,
        execute_live_trade=svc._execute_live_trade,
        safe_commit=svc._safe_commit,
        execute_paper_trade=svc._execute_paper_trade,
        record_midlong_factor_snapshots=svc._record_midlong_factor_snapshots,
    )


def _mark_block(code: str, *, detail: str = "") -> None:
    """[§52] 把本层拒绝原因登记给漏斗审计（不改行为，失败静默）。"""
    try:
        from backend.services.mlto.open_block_reason import mark_open_block
        mark_open_block(code, detail=detail, layer="proposal_execution")
    except Exception:
        pass


def evaluate_and_execute_proposal(
    *,
    db: Session,
    session,
    proposal,
    market_summary: dict,
    host: ProposalExecutionHost,
    session_mode: str = "running",
    strat=None,
) -> bool:
    from backend.services.decision_core.execute_proposal import evaluate_proposal
    from backend.services.decision_snapshot_writer import decision_snapshot_writer
    from backend.services.budget_service import budget_service
    from backend.services.orchestrator_derivatives import inject_derivatives_into_market_summary

    action = (proposal.action or "hold").lower()
    sym_u = proposal.symbol
    tier = proposal.tier
    trade_nature = proposal.trade_nature

    if session_mode not in ("running", "defensive") or action not in ("buy", "sell"):
        _mark_block("session_mode_or_action", detail=f"mode={session_mode} action={action}")
        return False

    if not host.midlong_persistence_allow(sym_u, trade_nature, action):
        try:
            from backend.config.settings import MIDLONG_PERSISTENCE_TICKS
            _pt = max(1, int(MIDLONG_PERSISTENCE_TICKS or 1))
        except Exception:
            _pt = 2
        logger.info("[Persistence] %s %s 拦截(未达 %dtick)", sym_u, trade_nature, _pt)
        _mark_block("persistence_ticks", detail=f"need={_pt}tick")
        return False

    if strat is None:
        strat = host.resolve_independent_strategy(db, session, sym_u, tier)
    if not strat:
        logger.info("[Agent独立] %s tier=%s 无 active 策略", sym_u, tier)
        _mark_block("no_active_strategy", detail=f"tier={tier}")
        return False

    # ── [调研轮37 2026-09-17] **空头来源限制：模板族策略不得开空** ──
    # 依据（30 天实测，用户指令「做空要认真做」）：
    #   * mid 空单整体：n=38 净 −68.60、均 **−1.81**、胜率 **26%**（多单对照均 −0.58 / 47%）；
    #     路径上逆行 +0.98% vs 顺行 0.22%（4.5 倍）；
    #   * 按来源拆：**`tpl_模板` 空单 27 笔、均 −2.12、胜率 30%** 是最大贡献者；
    #   * 空头"分位→收益"曲线（用位置闸拦下的 420 行空单做样本）：低分位 12h −0.71%/胜率 20%，
    #     被放行的 ≥40 分位同样亏 ⇒ **当前没有任何分位区间为正**，故不能靠调分位阈值解决；
    #   * 模板族在多头侧同样是最大亏损来源（long −127/21 笔）⇒ 让它**只提供证据、不驱动做空**。
    # 语义：仅拦**做空**且**解析到模板族策略**的开仓；多头与非模板来源空单不受影响。
    # 回滚：MIDLONG_SHORT_BLOCK_TEMPLATE_SOURCES=false。
    if action in ("sell",):
        try:
            from backend.config.settings import MIDLONG_SHORT_BLOCK_TEMPLATE_SOURCES as _sbt
        except Exception:
            _sbt = True
        _sid_now = str(getattr(strat, "strategy_id", "") or "")
        if _sbt and _sid_now.startswith("tpl_"):
            logger.info(
                "[Agent独立] %s tier=%s 模板族策略禁止开空（sid=%s）",
                sym_u, tier, _sid_now[:14],
            )
            _mark_block("short_template_source_block", detail=f"sid={_sid_now[:14]}")
            return False

    _trade_mode = host.session_trading_mode(session)
    account_id = int(getattr(session, "paper_account_id", None) or getattr(session, "account_id", None) or 0)
    mkt = (market_summary or {}).get(sym_u) or {}
    if isinstance(market_summary, dict):
        inject_derivatives_into_market_summary(market_summary, sym_u)
        mkt = market_summary.get(sym_u) or mkt

    verdict = evaluate_proposal(
        db=db,
        account_id=account_id,
        proposal=proposal,
        market_data=mkt if isinstance(mkt, dict) else {},
        mode=_trade_mode,
        persistence_allow=host.midlong_persistence_allow(sym_u, trade_nature, action),
    )

    host.persist_tcp_snapshot(
        session,
        symbol=sym_u,
        tier=tier,
        action=action,
        confidence=proposal.confidence,
        reasoning=proposal.reasoning,
        market_snapshot=mkt if isinstance(mkt, dict) else {},
        proposal=proposal.to_dict(),
        evaluate_verdict=verdict.to_dict(),
        source_lane=proposal.source_lane,
        trace_id=proposal.trace_id,
        proposal_id=proposal.proposal_id,
        executed=False,
        execution_channel=_trade_mode,
        strategy_id=str(getattr(strat, "strategy_id", "") or ""),
    )

    if not verdict.allowed:
        logger.info(
            "[V5Gate] BLOCK symbol=%s tier=%s action=%s detail=%s",
            sym_u, tier, action, verdict.reason,
        )
        _mark_block("v5gate", detail=str(verdict.reason or "")[:180])
        return False

    dec = proposal.to_decision_dict()
    _sm = float((verdict.adjustments or {}).get("size_multiplier") or 1.0)
    if _sm < 0.999:
        dec["size_multiplier"] = _sm
        logger.info("[V5Gate] DOWNSIZE symbol=%s tier=%s size×%.2f", sym_u, tier, _sm)

    try:
        _portfolio = host.build_portfolio_for_agents(db, session)
        _equity = float((_portfolio.get("balance") or {}).get("total_equity", 0) or 0)
        _bf = budget_service.scale_factor_for_layer(
            tier, _equity, _trade_mode, account_id=account_id
        )
        if _bf <= 0:
            logger.info("[BudgetService] %s 层预算已满，跳过新开", sym_u)
            _mark_block("budget_exhausted", detail=f"tier={tier}")
            return False
        if _bf < 1.0:
            dec["size_multiplier"] = float(dec.get("size_multiplier") or 1.0) * _bf
    except Exception:
        pass

    # ── S1-3 叠加 MTF 缩仓（逆高周期弱反向）——乘在预算/风控缩仓之上 ──
    try:
        _mtf_mult = float((getattr(proposal, "extra", None) or {}).get("mtf_size_mult") or 1.0)
        if _mtf_mult < 0.999:
            dec["size_multiplier"] = float(dec.get("size_multiplier") or 1.0) * _mtf_mult
            logger.info("[MidLongMTF] DOWNSIZE symbol=%s tier=%s size×%.2f", sym_u, tier, _mtf_mult)
    except Exception:
        pass

    # ── [Phase D 修复 Bug1] 叠加 tranche 分档保证金比例（staged-build）──
    # tranche_gate.compute_margin_pct 产出 0.30/0.30/0.20/0.10（按 tranche_stage 分档）。
    # 此前该值只写进 MltoTickResult 从不传到下单 → 分档建仓完全是死代码，实际下单
    # size 仅由 budget_service + V5Gate 决定（一次性满仓）。这里作为 size 乘子，
    # 乘在 budget/V5Gate/MTF 之后再缩，实现真正的分档试探/加仓。
    # 1.0 = 不缩（未传 / 非 MLTO 路径），< 1.0 = 缩仓，0.0 = tranche 已耗尽 → 跳过新开。
    try:
        _raw_tranche = (getattr(proposal, "extra", None) or {}).get("tranche_margin_pct")
        # 注意：不能用 `... or 1.0`，否则合法的 0.0（falsy）会被误判为缺省→1.0。
        _tranche_mult = 1.0 if _raw_tranche is None else float(_raw_tranche)
    except (TypeError, ValueError):
        _tranche_mult = 1.0
    if _tranche_mult <= 0.0:
        logger.info("[TrancheGate] %s tier=%s margin_pct=0%%（tranche 已耗尽）跳过新开", sym_u, tier)
        _mark_block("tranche_exhausted", detail=f"tier={tier}")
        return False
    if _tranche_mult < 0.999:
        dec["size_multiplier"] = float(dec.get("size_multiplier") or 1.0) * _tranche_mult
        logger.info(
            "[TrancheGate] DOWNSIZE symbol=%s tier=%s stage→size×%.2f", sym_u, tier, _tranche_mult,
        )

    logger.info("[V5Gate] PASS symbol=%s action=%s conf=%s nature=%s", sym_u, action, proposal.confidence, trade_nature)

    # 决策价一致性门禁：放行后、下单前，校验决策价与下单前实时价的偏离（默认关，见方法注释）
    _dp_ok, _dp_reason = host.decision_price_consistency_ok(sym_u, mkt, proposal, _trade_mode)
    if not _dp_ok:
        logger.info("[DecisionPriceGate] BLOCK symbol=%s tier=%s action=%s %s", sym_u, tier, action, _dp_reason)
        host.append_event(session, "decision_price_stale", f"[决策价过期] {sym_u} {_dp_reason[:120]}")
        _mark_block("decision_price_stale", detail=str(_dp_reason or "")[:180])
        return False

    if _trade_mode == "live":
        live_dec = dict(dec)
        live_dec.update({
            "operation": action,
            "symbol": sym_u,
            "side": action,
        })
        # [2026-08-29 实盘零成交·中线车道] 先封顶再送宪法检查：
        # 此前直接用未缩放敞口（available×pct×lev 兜底=33.2，永远超 20%
        # 上限）送检 → V5Gate PASS 的提案在宪法层被数学性锁死（实测
        # 17:47 ETH conf=79 PASS 后被拦）。与 execute_live_trade 内的
        # LiveCap 同一语义，这里必须先行——检查通过后 execute_live_trade
        # 内的封顶成为幂等 no-op。
        try:
            from backend.services.full_auto.live_trading import (
                _live_scale_decision_to_cap,
            )
            _live_scale_decision_to_cap(db, session, strat, live_dec, None)
        except Exception as _cap_err:
            logger.warning("[LiveCap] proposal 路径封顶跳过(fail-open): %s", _cap_err)
        # 缩放后仍低于交易所最小名义（币安 USDT-M 多数 $5）→ 诚实拒绝，
        # 不再无声 return False（此前的 evaluate_and_execute_returned_false
        # 无因可查）。
        try:
            _final_ov = float(live_dec.get("order_value") or 0)
            if 0 < _final_ov < 5.5:
                logger.info(
                    "[LiveDust] %s %s 缩放后名义 $%.2f < 交易所最小名义$5.5，跳过"
                    "（size_multiplier=%.4f）", sym_u, action, _final_ov,
                    float(live_dec.get("size_multiplier") or 1.0),
                )
                host.append_event(
                    session, "live_dust_block",
                    f"[Live最小名义] {sym_u} {action} 缩放后${_final_ov:.2f}<$5.5",
                )
                _mark_block("live_dust", detail=f"notional={_final_ov:.2f}")
                return False
        except Exception:
            pass
        _allowed, _risk_msg = host.live_constitutional_pre_trade_check(db, session, strat, live_dec)
        if not _allowed:
            host.append_event(session, "live_risk_block", f"[Live宪法] {sym_u} {_risk_msg[:100]}")
            _mark_block("live_constitutional", detail=str(_risk_msg or "")[:180])
            return False
        _live_ok = host.execute_live_trade(db, session, strat, live_dec)
        if not _live_ok:
            logger.info("[TCP] Live 下单未成功 %s tier=%s %s", sym_u, tier, action)
            _mark_block("live_order_failed", detail=f"{sym_u} {action}")
            return False
        host.safe_commit(db, "proposal_live_open", session=session)
        host.persist_tcp_snapshot(
            session,
            symbol=sym_u, tier=tier, action=action,
            confidence=proposal.confidence, reasoning=proposal.reasoning,
            proposal=proposal.to_dict(),
            evaluate_verdict=verdict.to_dict(),
            source_lane=proposal.source_lane,
            proposal_id=proposal.proposal_id,
            executed=True,
            execution_channel="live",
            strategy_id=str(getattr(strat, "strategy_id", "") or ""),
        )
        logger.info("[TCP] Live 已提交 %s tier=%s %s", sym_u, tier, action)
        return True

    ok = host.execute_paper_trade(db, session, strat, dec)
    if ok:
        host.safe_commit(db, "proposal_paper_open", session=session)
        host.persist_tcp_snapshot(
            session,
            symbol=sym_u, tier=tier, action=action,
            confidence=proposal.confidence, reasoning=proposal.reasoning,
            proposal=proposal.to_dict(),
            evaluate_verdict=verdict.to_dict(),
            source_lane=proposal.source_lane,
            proposal_id=proposal.proposal_id,
            executed=True,
            execution_channel="paper",
            strategy_id=str(getattr(strat, "strategy_id", "") or ""),
        )
        logger.info("[TCP] 已开单 %s tier=%s %s conf=%s", sym_u, tier, action, proposal.confidence)
        # ── S1-1 记录中长线开仓分数快照，供置信度校准（swing/trend）──
        # 写入一条 {swing|trend}_agent_score 反馈行，trade_id=持仓id、signal_value=分数。
        # 平仓时 paper_engine.update_trade_pnl 按 trade_id 自动回填盈亏，校准器据此
        # 把"分数→真实胜率"拟合出来（旁路，失败静默，不影响交易）。
        try:
            _nat = (trade_nature or "").lower()
            if _nat in ("swing", "trend_follow", "position"):
                from backend.services.calibration.confidence_calibrator import (
                    get_calibrator_for_nature,
                )
                from backend.database.models import PaperPosition
                _pos = (
                    db.query(PaperPosition)
                    .filter(
                        PaperPosition.account_id == account_id,
                        PaperPosition.symbol == sym_u,
                        PaperPosition.trade_nature == trade_nature,
                        PaperPosition.status == "open",
                    )
                    .order_by(PaperPosition.id.desc())
                    .first()
                )
                if _pos is not None:
                    get_calibrator_for_nature(_nat).record_score(
                        db=db,
                        account_id=account_id,
                        trade_id=int(_pos.id),
                        symbol=sym_u,
                        side=action,
                        score=float(proposal.confidence or 0),
                        direction="long" if action == "buy" else "short",
                    )
                    # S4-C：记录中长线活跃因子读数快照（factor:{fid}），供
                    # 平仓回填盈亏后按时间框架分流评估 IC（run_factor_ic_evaluation_segmented）。
                    host.record_midlong_factor_snapshots(
                        db=db, account_id=account_id, trade_id=int(_pos.id),
                        symbol=sym_u, side=action, market_data=mkt,
                    )

                    # ── S2-5c：把 LLM 的 exit_plan 写入持仓 exit_state_json ──
                    # 从 proposal.extra 读 tp_sl_proposal（S2-5b 传入），
                    # 转换为 NatureStagedTpState 读取的 tp_stages_override 格式。
                    # PEO tick 时会读 exit_state_json，用 LLM 分档覆盖默认 NATURE_EXIT_PROFILES。
                    try:
                        _extra = getattr(proposal, "extra", None) or {}
                        _tp_sl_prop = _extra.get("tp_sl_proposal") or {}
                        _tp_stages = _tp_sl_prop.get("tp_stages") if isinstance(_tp_sl_prop, dict) else None
                        _trailing_mult = _tp_sl_prop.get("trailing_atr_mult") if isinstance(_tp_sl_prop, dict) else None
                        _invalidation = _extra.get("invalidation_condition") or ""
                        _exp_hold = float(_extra.get("expected_hold_hours") or 0)

                        _exit_state = {}
                        import json as _json
                        try:
                            _exit_state = _json.loads(getattr(_pos, "exit_state_json", None) or "{}")
                        except Exception:
                            _exit_state = {}
                        if not isinstance(_exit_state, dict):
                            _exit_state = {}

                        # 写入 nature_staged_tp 子状态（PEO 读这个）
                        _nstp_state = _exit_state.get("nature_staged_tp") or {}
                        if _tp_stages and isinstance(_tp_stages, list):
                            _nstp_state["tp_stages_override"] = _tp_stages
                        if _trailing_mult and float(_trailing_mult) > 0:
                            _nstp_state["trailing_atr_mult_override"] = float(_trailing_mult)
                        _exit_state["nature_staged_tp"] = _nstp_state

                        # 写入 invalidation + expected_hold（TrendAgent review / exit_state_machine 读）
                        if _invalidation:
                            _exit_state["invalidation_condition"] = str(_invalidation)[:300]
                        if _exp_hold > 0:
                            _exit_state["expected_hold_hours"] = _exp_hold
                        _exit_state["lifecycle_state"] = "initial"
                        # [M1-A 2026-08-21] 落库 entry_source：出场分流据此识别
                        # 因子仓（factor_route 仓禁方向复查碎平）。历史仓无键
                        # → unknown，保持现有复查行为不变。
                        _entry_src = str(_extra.get("entry_source") or "").strip().lower()
                        if _entry_src:
                            _exit_state["entry_source"] = _entry_src

                        _pos.exit_state_json = _json.dumps(_exit_state, ensure_ascii=False)
                        if _exp_hold > 0 and not getattr(_pos, "expected_hold_hours", None):
                            _pos.expected_hold_hours = _exp_hold
                        logger.info(
                            "[S2-5] %s pos#%d exit_plan 写入: tp_stages=%s trailing_mult=%s invalidation=%s",
                            sym_u, _pos.id,
                            f"{len(_tp_stages)}档" if _tp_stages else "默认",
                            _trailing_mult or "默认",
                            "有" if _invalidation else "无",
                        )
                    except Exception as _exit_plan_err:
                        logger.debug("[S2-5] %s exit_plan 写入跳过: %s", sym_u, _exit_plan_err)
        except Exception as _cal_err:
            logger.debug("[MidLongCalibrator] %s 分数快照记录跳过: %s", sym_u, _cal_err)
    if not ok:
        # [§52] 兜底：只有在下游没有登记更具体原因时才写通用码。
        try:
            from backend.services.mlto.open_block_reason import peek_open_block
            if not peek_open_block():
                _mark_block("paper_trade_false", detail="execute_paper_trade returned False")
        except Exception:
            pass
    return ok
