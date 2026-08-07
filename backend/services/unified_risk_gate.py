"""
UnifiedRiskGate — 统一风控入口（2026-05-08 深挖第 2 项）

把项目里两套并存的风控合并到一个 facade，让 paper / live / FullAuto 走相同入口：

  Layer 1 ─ DeterministicRiskGate（无状态硬规则）
            · max_symbol_notional_pct
            · max_side_margin_pct
            · max_daily_loss_pct
            · max_portfolio_leverage
            · min_available_balance_pct

  Layer 2 ─ RiskControlService.check_all（带状态规则 / 需要 db 查历史）
            · daily_loss_breaker（熔断恢复状态从 DB 读）
            · margin_usage
            · single_symbol_limit
            · total_position_limit
            · max_position_per_trade（20% 硬上限）
            · max_daily_trades
            · max_symbol_entries_per_day
            · consecutive_loss_pause / reduce
            · strategy_consecutive_losses（可选）

任何一层 BLOCKED 都返回 passed=False，并按统一 schema 写入 risk_control_events，
event_type="unified_blocked"，details 字段为 JSON 字符串包含 {layer, rule, reason}。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from backend.services.deterministic_risk_gate import (
    AccountSnapshot,
    PositionInfo,
    ProposedOrder,
    RiskCheckResult,
    risk_gate as _det_gate,
)

logger = logging.getLogger(__name__)


@dataclass
class UnifiedRiskResult:
    passed: bool
    blocked_layer: str = ""           # "deterministic" / "stateful" / ""
    blocked_rule: str = ""            # 触发的规则名
    reason_text: str = ""             # 人类可读
    reason_code: str = ""
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    layer_results: Dict[str, Any] = field(default_factory=dict)


def unified_check(
    db: Session,
    *,
    account_id: int,
    symbol: str,
    side: str,                  # "buy" / "sell"
    notional: float,
    margin: float,
    leverage: float,
    total_equity: float,
    available_balance: float,
    frozen_margin: float,
    realized_pnl_today: float = 0.0,
    margin_usage_percent: float = 0.0,
    existing_positions: Optional[List[Dict[str, Any]]] = None,
    write_event: bool = True,
    op_source: str = "unknown",     # "paper" / "live" / "full_auto"
) -> UnifiedRiskResult:
    """统一风控检查 — 任意一层失败即返回不通过。

    existing_positions 字段约定（dict）:
        symbol, side ("long"/"short"), margin, notional, size, leverage
    """
    res = UnifiedRiskResult(passed=True)
    existing_positions = existing_positions or []

    # ────────────── Layer 1: 无状态硬规则 ──────────────
    try:
        det_account = AccountSnapshot(
            total_equity=total_equity,
            available_balance=available_balance,
            frozen_margin=frozen_margin,
            realized_pnl_today=realized_pnl_today,
        )
        det_positions = [
            PositionInfo(
                symbol=p.get("symbol", ""),
                side=p.get("side", "long"),
                margin=float(p.get("margin", 0)),
                notional=float(p.get("notional", 0)),
                size=float(p.get("size", 0)),
                leverage=float(p.get("leverage", 1)),
            )
            for p in existing_positions
        ]
        det_order = ProposedOrder(
            symbol=symbol, side=side, notional=notional, margin=margin, leverage=leverage
        )
        det_result: RiskCheckResult = _det_gate.check(det_account, det_positions, det_order)
        res.layer_results["deterministic"] = {
            "passed": det_result.passed,
            "blocked_by": det_result.blocked_by,
            "reason_text": det_result.reason_text,
            "reason_code": det_result.reason_code,
        }
        if not det_result.passed:
            res.passed = False
            res.blocked_layer = "deterministic"
            res.blocked_rule = det_result.blocked_by
            res.reason_text = det_result.reason_text
            res.reason_code = det_result.reason_code
            if write_event:
                _persist_event(db, account_id, symbol, side, op_source, res)
            return res
    except Exception as e:
        # [fix] rollback 避免后续 InFailedSqlTransaction 污染
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning(
            f"[UnifiedRiskGate] DeterministicRiskGate 异常（放行该层）: {e}",
            exc_info=True,
        )

    # ────────────── Layer 2: 带状态规则 ──────────────
    try:
        from backend.services.risk_control_service import (
            get_risk_control_service,
            RiskCheckResult as _RC2,
        )
        svc = get_risk_control_service()
        try:
            svc.load_config_from_db(db, account_id)
        except Exception as _ce:
            # [fix] rollback 避免后续 InFailedSqlTransaction 污染
            try:
                db.rollback()
            except Exception:
                pass
            logger.debug(f"[UnifiedRiskGate] load_config_from_db 跳过: {_ce}")

        positions_for_svc = [
            {
                "symbol": p.get("symbol"),
                "side": p.get("side"),
                "size": float(p.get("size", 0)),
                "value": float(p.get("notional", 0)),
                "leverage": float(p.get("leverage", 1)),
            }
            for p in existing_positions
        ]
        passed, responses = svc.check_all(
            db=db,
            account_id=account_id,
            symbol=symbol,
            operation=side,                    # "buy" / "sell"
            order_value=notional,
            total_equity=total_equity,
            available_balance=available_balance,
            positions=positions_for_svc,
            margin_usage_percent=margin_usage_percent,
            order_margin=margin,                  # 2026-05-08 深挖第 4 轮：传 margin 给保证金基准检查
        )
        res.layer_results["stateful"] = {
            "passed": passed,
            "responses": [
                {
                    "result": r.result.value if hasattr(r.result, "value") else str(r.result),
                    "check_name": r.check_name,
                    "message": r.message,
                }
                for r in responses
            ],
        }
        if not passed:
            blocked = next(
                (r for r in responses if r.result == _RC2.BLOCKED),
                None,
            )
            if blocked:
                res.passed = False
                res.blocked_layer = "stateful"
                res.blocked_rule = blocked.check_name
                res.reason_text = blocked.message
                res.reason_code = blocked.check_name
                if write_event:
                    _persist_event(db, account_id, symbol, side, op_source, res)
                return res
        # 收集 warnings（不阻塞）
        for r in responses:
            if r.result == _RC2.WARNING:
                res.warnings.append(
                    {"rule": r.check_name, "message": r.message}
                )
    except Exception as e:
        # [fix] rollback 避免后续 InFailedSqlTransaction 污染
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning(
            f"[UnifiedRiskGate] RiskControlService 异常（放行该层）: {e}",
            exc_info=True,
        )

    return res


def record_guard_block(
    db: Session,
    *,
    account_id: int,
    guard_name: str,                # "reentry_cooldown" / "fee_guard" / ...
    symbol: str = "",
    side: str = "",
    reason: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """guard 模块拦截事件统一落盘 (event_type='guard_blocked')。

    供 reentry_cooldown / fee_guard / liquidity_filter / liquidation_monitor /
    master_close_guard / profit_drawdown_guard 在做出"不允许"判断后调用，
    把"为什么没下单"持久化到 risk_control_events 表。
    """
    try:
        from backend.database.models import RiskControlEvent
        payload: Dict[str, Any] = {
            "guard_name": guard_name,
            "symbol": symbol,
            "side": side,
            "reason": reason,
        }
        if extra:
            payload.update({k: v for k, v in extra.items() if k not in payload})
        db.add(
            RiskControlEvent(
                account_id=account_id,
                event_type="guard_blocked",
                details=json.dumps(payload, ensure_ascii=False),
            )
        )
        db.flush()
    except Exception as e:
        # [fix] rollback 避免后续 InFailedSqlTransaction 污染
        try:
            db.rollback()
        except Exception:
            pass
        logger.debug(f"[UnifiedRiskGate] guard 拦截事件落库跳过 ({guard_name}): {e}")


def _persist_event(
    db: Session,
    account_id: int,
    symbol: str,
    side: str,
    op_source: str,
    res: UnifiedRiskResult,
) -> None:
    """统一拒单事件落库（risk_control_events.event_type='unified_blocked'）"""
    try:
        from backend.database.models import RiskControlEvent
        db.add(
            RiskControlEvent(
                account_id=account_id,
                event_type="unified_blocked",
                details=json.dumps(
                    {
                        "symbol": symbol,
                        "side": side,
                        "op_source": op_source,
                        "blocked_layer": res.blocked_layer,
                        "blocked_rule": res.blocked_rule,
                        "reason_text": res.reason_text,
                        "reason_code": res.reason_code,
                        "warnings": res.warnings,
                        "layer_results": res.layer_results,
                    },
                    ensure_ascii=False,
                ),
            )
        )
        db.flush()
    except Exception as e:
        # [fix] flush 失败会将 session 置于 InFailedSqlTransaction 状态，
        # 必须 rollback 清除，否则后续所有操作（包括交易执行）全部连锁失败。
        try:
            db.rollback()
        except Exception:
            pass
        logger.debug(f"[UnifiedRiskGate] 拒单事件落库跳过: {e}")
