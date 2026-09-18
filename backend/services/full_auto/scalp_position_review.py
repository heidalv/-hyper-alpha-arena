"""scalp 持仓复审（PostFill Position Agent · Phase 1-3，2026-08-30）。

背景：scalp 循环此前只管开仓（明文注释"本循环不负责平仓"），持仓期间
每 45s 仍对持仓币跑全套入场分析（算力浪费），真正的仓位管理只剩
paper monitor 3s 引擎硬层。本模块补上"开仓后分析模式切换"：

  该币已有 open scalp 仓 → 跳过入场分析重路径，改为轻量持仓监视：
  构造 PositionContext → unified_exit_state_machine 仲裁（保护层全生效）
  → 执行 ShortTierExit 的认错/保本/ATR尾随/翻脸收紧（从"建议"到"执行"）。

职责边界（防双头管理）：
  - 分批止盈（REDUCE）不在此执行——统一归 paper 引擎的
    _run_unified_staged_tp（含 minNotional/费用预算可行性门）。
  - 本模块只执行：fast_cut 认错全平 / breakeven 保本推进 / regime_flip
    收紧 / trailing 回撤止盈全平。每仓每 tick 至多一个实质动作。
  - live 会话平仓走 svc._close_position_live_aware（经 LPM 账本）；
    SL 收紧走 paper_engine.update_position_tp_sl（live_tpsl_sync 随后镜像
    到交易所端挂单）。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _hold_seconds(pos) -> int:
    """已持仓秒数（opened_at 是北京钟面 naive，与引擎同口径归一化 UTC）。"""
    opened = getattr(pos, "opened_at", None)
    if not opened:
        return 0
    try:
        from backend.utils.db_datetime import parse_db_naive_to_utc
        _n = parse_db_naive_to_utc(opened)
        if _n is not None:
            opened = _n
    except Exception:
        pass
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - opened).total_seconds()))


def _atr_pct_from_md(md: Dict[str, Any]) -> float:
    """ATR 百分比（5m K线 ATR14 / 现价 ×100）。K线缺失退 UnifiedDataPool，再退 0.8。"""
    try:
        kl = md.get("klines")
        if kl is not None and hasattr(kl, "iloc") and len(kl) >= 15:
            import pandas as pd
            h = pd.to_numeric(kl["high"], errors="coerce")
            l = pd.to_numeric(kl["low"], errors="coerce")
            c_prev = pd.to_numeric(kl["close"], errors="coerce").shift(1)
            tr = pd.concat([(h - l), (h - c_prev).abs(), (l - c_prev).abs()], axis=1).max(axis=1)
            atr = tr.rolling(14, min_periods=5).mean().iloc[-1]
            last = float(pd.to_numeric(kl["close"], errors="coerce").iloc[-1])
            if atr > 0 and last > 0:
                return float(atr / last * 100.0)
    except Exception:
        pass
    return 0.8


def _breakeven_reached(pos) -> bool:
    """SL 是否已推进到盈利侧（engine 的统一块 TP1 后会做，避免状态机重复推）。"""
    sl = float(getattr(pos, "sl_price", 0) or 0)
    entry = float(getattr(pos, "entry_price", 0) or 0)
    if sl <= 0 or entry <= 0:
        return False
    side = str(getattr(pos, "side", "long")).lower()
    return sl > entry if side in ("long", "buy") else sl < entry


def _observe_only_enabled() -> bool:
    """[PostFill §3.7 2026-08-31] observe_only 影子模式：只记录决策不执行。

    无 A/B 直接全开后的补位：POSTFILL_OBSERVE_ONLY=true 时 scalp 持仓复审
    把"若执行"的决策落 position_exit_events 事件流（不实际平仓/改 SL），
    供假设 PnL 对照。默认 false（不影响生产）。
    """
    try:
        import os as _os_oo
        return str(_os_oo.getenv("POSTFILL_OBSERVE_ONLY", "false")).strip().lower() in (
            "1", "true", "yes", "on",
        )
    except Exception:
        return False


def run_scalp_position_review(
    svc, db, session_row, account_id: int, symbol: str,
    positions: List[Any], md: Dict[str, Any],
) -> Dict[str, Any]:
    """对某 symbol 的全部 open scalp 仓做一轮状态机复审并执行。

    svc: FullAutoTradingService（用 _close_position_live_aware）。
    session_row: FullAutoSession DB 行（判 live/paper）。
    positions: PaperPosition ORM 对象列表（trade_nature=scalp）。
    md: 该币的市场快照（需含 klines/price）。

    返回 {symbol, reviewed, actions: [{position_id, action, source, reason}]}。
    任何异常都被吞掉（复审失败不影响交易主链）。
    """
    out: Dict[str, Any] = {"symbol": symbol, "reviewed": 0, "actions": []}
    if not positions:
        return out

    from backend.services.paper_trading_engine import PaperTradingEngine, paper_engine
    from backend.services.exit.exit_types import (
        ExitAction,
        ExitRequest,
        ExitSource,
        ExitUrgency,
        PositionContext,
    )
    from backend.services.exit.unified_exit_state_machine import exit_state_machine

    price = 0.0
    for key in ("price", "mark_price", "current_price"):
        try:
            v = float(md.get(key) or 0)
            if v > 0:
                price = v
                break
        except Exception:
            continue

    atr_pct = _atr_pct_from_md(md)
    _orch = md.get("orchestrator") if isinstance(md.get("orchestrator"), dict) else {}
    regime = str(md.get("regime") or _orch.get("regime") or "")

    for pos in positions:
        try:
            if str(getattr(pos, "status", "")) != "open":
                continue
            pos_id = int(getattr(pos, "id", 0) or 0)
            if pos_id <= 0:
                continue
            out["reviewed"] += 1

            px = price or float(getattr(pos, "mark_price", 0) or 0) or float(pos.entry_price or 0)
            if px <= 0:
                continue
            side = str(pos.side or "long").lower()
            pnl_pct = PaperTradingEngine._position_pnl_pct(pos, px) * 100.0
            peak_pct = float(getattr(pos, "peak_pnl_pct", 0.0) or 0.0) * 100.0
            tp_level = int(getattr(pos, "tp_level_reached", 0) or 0)

            # 引擎真实状态 → 状态机对齐（防跨入口重复推 breakeven/trailing）
            exit_state_machine.sync_position_state(
                pos_id,
                tp_level_reached=tp_level,
                breakeven_active=_breakeven_reached(pos),
                trailing_active=tp_level >= 3,
                peak_pnl_pct=peak_pct,
            )

            ctx = PositionContext(
                position_id=pos_id, symbol=symbol, tier="short", side=side,
                entry_price=float(pos.entry_price or 0), current_price=px,
                quantity=float(pos.size or 0),
                leverage=float(pos.leverage or 1),
                sl_price=float(getattr(pos, "sl_price", 0) or 0) or None,
                tp_price=float(getattr(pos, "tp_price", 0) or 0) or None,
                unrealized_pnl_pct=pnl_pct,
                peak_pnl_pct=peak_pct,
                hold_seconds=_hold_seconds(pos),
                atr_pct=atr_pct,
                tp_level_reached=tp_level,
                regime=regime,
                funding_rate=float(md.get("funding_rate") or 0),
            )
            req = ExitRequest(
                position_id=pos_id, symbol=symbol, tier="short",
                source=ExitSource.HOLD_REVIEW.value,
                proposed_action=ExitAction.HOLD.value,
                urgency=ExitUrgency.NORMAL.value,
                reason_detail="scalp_position_review",
                ts_ns=int(time.time() * 1e9),
            )
            decision = exit_state_machine.submit(req, ctx)
            if not decision or decision.action in (ExitAction.HOLD.value, ExitAction.DEFER.value):
                continue

            acted: Dict[str, Any] = {
                "position_id": pos_id, "action": decision.action,
                "source": decision.source, "reason": decision.reason,
            }

            if _observe_only_enabled():
                # [PostFill §3.7] 影子模式：只记不执行（A/B 假设 PnL 对照用）。
                try:
                    paper_engine._record_exit_event(
                        db, pos,
                        event_type="postfill_observe_only_decision",
                        exit_channel="postfill_observe_only",
                        metadata={
                            "decision_action": str(decision.action),
                            "source": str(decision.source),
                            "reason": str(decision.reason),
                            "new_sl_price": float(decision.new_sl_price or 0),
                        },
                    )
                except Exception:
                    pass
                acted["executed"] = False
                acted["observe_only"] = True
                out["actions"].append(acted)
                continue

            if decision.action == ExitAction.TIGHTEN_SL.value and decision.new_sl_price:
                new_sl = float(decision.new_sl_price)
                cur_sl = float(getattr(pos, "sl_price", 0) or 0)
                side_mult = 1 if side in ("long", "buy") else -1
                improves = (side_mult > 0 and new_sl > cur_sl) or (side_mult < 0 and (cur_sl <= 0 or new_sl < cur_sl))
                if improves:
                    ok = paper_engine.update_position_tp_sl(
                        db, pos_id, sl_price=new_sl,
                        # [轮96 Fix C] 复查给出的收紧属追踪派生，标注来源以便
                        # paper 引擎在 min_hold 保护期内拒付（短线车道保护期 2h）。
                        sl_source="trailing",
                    )
                    acted["executed"] = bool(ok)
                    logger.info(
                        "[PostFill][ScalpReview] %s %s SL收紧→%.6f (%s) %s",
                        symbol, side, new_sl, decision.source,
                        "成功" if ok else "失败",
                    )
                else:
                    acted["executed"] = False
            elif decision.action == ExitAction.CLOSE.value:
                res = svc._close_position_live_aware(
                    db, session_row, account_id, symbol, side,
                    reason=f"scalp_review_{decision.source or 'exit'}"[:100],
                    strategy_id=getattr(pos, "strategy_id", None),
                    trade_nature="scalp",
                )
                # [轮74] 不得再用 `bool(res)`：dict 恒为真，会把 live 的
                # status="error"/"blocked" 读成「已平」。按结果判定。
                from backend.services.exit.exit_types import close_result_succeeded
                if close_result_succeeded(res):
                    acted["executed"] = True
                    logger.info(
                        "[PostFill][ScalpReview] %s %s 全平(%s): %s pnl=%s",
                        symbol, side, decision.source, decision.reason,
                        (res or {}).get("pnl"),
                    )
                else:
                    acted["executed"] = False
                    acted["error"] = f"平仓未确认成交 status={(res or {}).get('status')}"
                    logger.warning(
                        "[PostFill][ScalpReview] %s %s 平仓未确认成交 status=%s —— 不记 executed",
                        symbol, side, (res or {}).get("status"),
                    )
            elif decision.action == ExitAction.REDUCE.value:
                # 分批止盈统一归引擎 _run_unified_staged_tp（含可行性门），此处不执行
                acted["executed"] = False
                acted["skipped"] = "reduce_delegated_to_engine"
            else:
                acted["executed"] = False

            out["actions"].append(acted)
        except Exception as pos_err:
            logger.warning(
                "[PostFill][ScalpReview] %s pos#%s 复审异常(跳过): %s",
                symbol, getattr(pos, "id", "?"), pos_err,
            )
    return out
