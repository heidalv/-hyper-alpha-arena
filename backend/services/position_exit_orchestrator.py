"""PositionExitOrchestrator — rule-first staged TP / trailing executor."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class PositionExitOrchestrator:
    def evaluate_and_execute(
        self,
        *,
        db,
        account_id: int,
        positions: List[Dict[str, Any]],
        market_summary: Dict[str, Any],
        session=None,
        append_event=None,
    ) -> int:
        """Evaluate open positions and execute hard rule exits.

        Returns the number of actual position changes.
        """
        from backend.database.models import PaperPosition
        from backend.services.nature_staged_tp import (
            NatureStagedTpState, NatureStagedTpDecision, check,
        )
        from backend.services.paper_trading_engine import paper_engine

        # Phase B+C: 当 v2 统一分段止盈(RISK_V2_UNIFIED_STAGED_TP)开启时,
        # 分段 TP + 追踪止损已由 _run_v2_protection 统一处理。此处跳过
        # nature_staged_tp.check 的 reduce/trailing 动作, 避免与 v2 双触发。
        # breakeven/invalidation 状态机路径仍保留(互补职责)。
        try:
            from backend.config.settings import RISK_V2_UNIFIED_STAGED_TP as _v2_unified
        except Exception:
            _v2_unified = True

        # S2-6 调试日志:确认 PEO 被调用
        _midlong_positions = [p for p in (positions or [])
                               if (p.get("trade_nature") in ("swing", "trend_follow", "position")
                                   or p.get("timeframe_tier") in ("mid", "long"))]
        logger.info(
            f"[PEO] evaluate_and_execute called: positions={len(positions or [])} "
            f"midlong={len(_midlong_positions)} account={account_id}"
        )

        changes = 0
        for p in positions or []:
            pid = p.get("id")
            sym = p.get("symbol")
            side = p.get("side")
            entry = float(p.get("entry_price", 0) or 0)
            mark = float(p.get("mark_price", 0) or entry)
            nature = p.get("trade_nature") or "swing"
            if not pid or not sym or not side or entry <= 0 or mark <= 0:
                continue

            db_pos = db.query(PaperPosition).filter(
                PaperPosition.id == int(pid),
                PaperPosition.status == "open",
            ).first()
            if not db_pos:
                continue

            state_data = {}
            try:
                state_data = json.loads(getattr(db_pos, "exit_state_json", None) or "{}")
            except Exception:
                state_data = {}
            state = NatureStagedTpState.from_dict(state_data.get("nature_staged_tp") or state_data)

            mkt = (market_summary or {}).get(sym, {}) if isinstance(market_summary, dict) else {}
            atr_pct = float((mkt or {}).get("volatility_value", 0.02) or 0.02)

            # ── TrendAgent 止盈止损优化（2026-06-18）──
            # 读 exit_state_json 里的 trend_adjustment，动态调整 trailing/staged TP。
            _trend_adj = state_data.get("trend_adjustment") or {}
            _trailing_mult = _trend_adj.get("trailing_atr_mult")
            if _trailing_mult is not None:
                # trailing ATR 倍数影响：mult>2 放宽 trailing（让利润奔跑），
                # mult<2 收紧 trailing（锁定利润）。通过缩放 atr_pct 实现。
                _scale = float(_trailing_mult) / 2.0  # 默认 mult=2 → scale=1.0
                atr_pct = atr_pct * _scale
            _staged_adj = _trend_adj.get("staged_tp_adjust")
            # staged_tp_adjust: raise=提高触发点（趋势强让利润跑更久），lower=降低（趋势弱提前止盈）
            # 通过缩放 entry-price 距离实现（在 check 内部由 atr_pct 间接影响）

            decision = check(
                entry_price=entry,
                current_price=mark,
                side=side,
                trade_nature=nature,
                atr_pct=atr_pct,
                state=state,
            )

            # Phase B+C: v2 统一分段 TP 接管时, 旁路 PEO 的 reduce/trailing 动作。
            # state 仍更新(保留 peak_pnl_pct 等观测), 但不执行平仓/改 SL。
            if _v2_unified:
                decision = NatureStagedTpDecision(action="hold")

            from backend.services.position_exit_state import merge_exit_state, dump_exit_state

            db_pos.exit_state_json = dump_exit_state(
                merge_exit_state(state_data, {"nature_staged_tp": state.to_dict()}),
            )
            db_pos.peak_pnl_pct = max(float(getattr(db_pos, "peak_pnl_pct", 0.0) or 0.0), state.peak_pnl_pct)
            db.flush()

            # ── S2-6：调用 unified_exit_state_machine 做 breakeven/invalidation 仲裁 ──
            # nature_staged_tp 只管分批 TP + trailing，不覆盖 breakeven push 和 invalidation 退出。
            # 这里把同一持仓提交给 unified 状态机，让 tier_exit_strategies 的 breakeven/invalidation
            # 也能触发。两套机制互补：nature_staged_tp 管分档 TP/trailing，unified 管 breakeven/invalidation。
            try:
                from backend.services.exit.unified_exit_state_machine import exit_state_machine
                from backend.services.exit.exit_types import (
                    ExitRequest, PositionContext, ExitSource, ExitAction, ExitUrgency,
                )
                from backend.services.exit.unified_exit_state_machine import PositionExitState
                # 构造 PositionContext（从持仓 + state 读取）
                _tier = (p.get("timeframe_tier") or ("long" if nature in ("trend_follow", "position") else "mid")).lower()
                _pnl_pct = state.peak_pnl_pct  # 用 nature_staged_tp 已算的 peak
                _current_pnl = ((mark - entry) / entry * 100) if side in ("long", "buy") else ((entry - mark) / entry * 100)
                # 读 invalidation_condition（S2-5c 写入的）
                _invalidation = state_data.get("invalidation_condition", "")
                _tp_stages = (state_data.get("nature_staged_tp") or {}).get("tp_stages_override")
                _ctx = PositionContext(
                    position_id=int(pid), symbol=sym, tier=_tier, side=side,
                    entry_price=entry, current_price=mark, quantity=float(p.get("size", 0) or 0),
                    leverage=float(p.get("leverage", 1) or 1),
                    sl_price=float(getattr(db_pos, "sl_price", 0) or 0) or None,
                    tp_price=float(getattr(db_pos, "tp_price", 0) or 0) or None,
                    unrealized_pnl_pct=_current_pnl,
                    peak_pnl_pct=_pnl_pct,
                    hold_seconds=int((db_pos.closed_at or __import__('datetime').datetime.utcnow() - db_pos.opened_at).total_seconds()) if db_pos.opened_at else 0,
                    atr_pct=atr_pct * 100,
                    tp_stages=_tp_stages if isinstance(_tp_stages, list) else [],
                    tp_level_reached=len(state.triggered_stages),
                    invalidation_condition=_invalidation,
                )
                _req = ExitRequest(
                    position_id=int(pid), symbol=sym, tier=_tier,
                    source=ExitSource.HOLD_REVIEW.value,
                    proposed_action=ExitAction.HOLD.value,
                    urgency=ExitUrgency.NORMAL.value,
                    reason_detail="peo_lifecycle_check",
                )
                _sm_decision = exit_state_machine.submit(_req, _ctx)
                if _sm_decision and _sm_decision.action == ExitAction.TIGHTEN_SL.value and _sm_decision.new_sl_price:
                    # breakeven push：更新 SL
                    paper_engine.update_position_tp_sl(db, int(pid), sl_price=_sm_decision.new_sl_price)
                    self._event(append_event, session, "lifecycle_breakeven",
                                f"{sym}[{nature}] breakeven SL→${_sm_decision.new_sl_price:.4f}")
                    changes += 1
                elif _sm_decision and _sm_decision.action == ExitAction.CLOSE.value:
                    # invalidation 退出：全平
                    res = paper_engine.close_position(
                        db, account_id, sym, side,
                        reason=f"lifecycle_invalidation",
                        strategy_id=p.get("strategy_id"),
                    )
                    if res:
                        changes += 1
                        self._bump_session(session, res.get("pnl", 0))
                        self._event(append_event, session, "lifecycle_invalidation",
                                    f"{sym}[{nature}] invalidation 全平 PnL=${res.get('pnl', 0):+.2f}")
            except Exception as _sm_err:
                logger.debug("[PEO][S2-6] %s exit_state_machine 调用跳过: %s", sym, _sm_err)

            if decision.action == "reduce" and decision.reduce_ratio > 0:
                qty = round(float(p.get("size", 0) or 0) * float(decision.reduce_ratio), 8)
                if qty <= 0:
                    continue
                reason = f"nature_tp_staged_{(decision.stage_idx or 0) + 1}"
                res = paper_engine.close_position(
                    db, account_id, sym, side,
                    reason=reason, quantity=qty,
                    strategy_id=p.get("strategy_id"),
                )
                if res:
                    changes += 1
                    self._bump_session(session, res.get("pnl", 0))
                    self._event(append_event, session, "nature_staged_tp",
                                f"{sym}[{nature}] 分批TP{(decision.stage_idx or 0) + 1}: "
                                f"减仓{decision.reduce_ratio:.0%} PnL=${res.get('pnl', 0):+.2f}")

            elif decision.action == "trailing_hit":
                res = paper_engine.close_position(
                    db, account_id, sym, side,
                    reason="nature_trailing_hit",
                    strategy_id=p.get("strategy_id"),
                )
                if res:
                    changes += 1
                    self._bump_session(session, res.get("pnl", 0))
                    self._event(append_event, session, "nature_trailing_hit",
                                f"{sym}[{nature}] trailing hit 全平 PnL=${res.get('pnl', 0):+.2f}")

            elif decision.action == "trailing_update" and decision.suggested_sl_price:
                paper_engine.update_position_tp_sl(db, int(pid), sl_price=decision.suggested_sl_price)
                self._event(append_event, session, "nature_trailing_update",
                            f"{sym}[{nature}] trailing SL→${decision.suggested_sl_price}")

        return changes

    @staticmethod
    def _bump_session(session, pnl: float) -> None:
        if not session:
            return
        session.total_trades = (session.total_trades or 0) + 1
        if float(pnl or 0) > 0:
            session.winning_trades = (session.winning_trades or 0) + 1

    @staticmethod
    def _event(append_event, session, event_type: str, text: str) -> None:
        try:
            if append_event and session:
                append_event(session, event_type, text)
        except Exception:
            logger.debug("[PEO] append event failed", exc_info=True)


position_exit_orchestrator = PositionExitOrchestrator()
