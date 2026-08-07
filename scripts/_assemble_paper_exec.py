"""Assemble paper_execution.py from extracted body."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
body_path = ROOT / "backend/services/full_auto/_paper_exec_body.tmp"
body = body_path.read_text(encoding="utf-8")

# normalize host method names (strip leading underscore on host API)
for name in (
    "ensure_bound_strategy",
    "get_trading_account_id",
    "extract_ai_position_pct",
    "apply_auto_coin_position_scale",
    "append_event",
    "get_today_realized_pnl",
    "get_validated_trade_nature",
    "recover_db_session",
    "is_unified_executor_on",
):
    body = body.replace(f"host._{name}", f"host.{name}")

nature_old = body[body.index("# ── 解析 trade_nature"): body.index("# ── 子仓位管理器审核开仓")]
nature_new = """            _trade_nature, _sub_tier = resolve_sub_tier_and_nature(
                strat=strat,
                decision=decision,
                timeframe_tier=timeframe_tier,
                symbol=symbol,
                market_scan_cache=host.market_scan_cache,
                get_validated_trade_nature=host.get_validated_trade_nature,
                valid_trade_natures=host.valid_trade_natures,
            )

"""
body = body.replace(nature_old, nature_new)

tp_old_start = body.index("# ── P0-3 强制 TP/SL 兜底")
tp_old_end = body.index("_plan_hold_h = float(")
tp_new = """            _final_sl, _final_tp = finalize_open_tp_sl(
                symbol=symbol,
                trade_nature=_trade_nature,
                side=side,
                price=price,
                plan_sl=plan.stop_loss_price,
                plan_tp=plan.take_profit_price,
                is_auto_coin=_is_auto_coin,
                on_event=lambda et, msg: host.append_event(session, et, msg),
            )

"""
body = body[:tp_old_start] + tp_new + body[tp_old_end:]

header = '''"""Paper 模拟下单执行 — 从 monolith _execute_paper_trade 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Set

from sqlalchemy.orm import Session

from backend.services.full_auto.paper_nature import resolve_sub_tier_and_nature
from backend.services.full_auto.paper_tp_sl import finalize_open_tp_sl

logger = logging.getLogger(__name__)


@dataclass
class PaperExecutionHost:
    """monolith 状态与回调切片，供 execute_paper_trade 使用。"""

    market_scan_cache: Dict[str, Any]
    template_recent_opens: Dict[str, Any]
    recovery_until: Dict[str, float]
    recovery_position_scale: float
    valid_trade_natures: Set[str]
    sub_mgr: Any
    ensure_bound_strategy: Callable
    get_trading_account_id: Callable
    extract_ai_position_pct: Callable
    apply_auto_coin_position_scale: Callable
    append_event: Callable
    get_today_realized_pnl: Callable
    get_validated_trade_nature: Callable
    recover_db_session: Callable
    is_unified_executor_on: Callable


def build_paper_execution_host(svc) -> PaperExecutionHost:
    if not hasattr(svc, "_template_recent_opens"):
        svc._template_recent_opens = {}
    return PaperExecutionHost(
        market_scan_cache=svc._market_scan_cache,
        template_recent_opens=svc._template_recent_opens,
        recovery_until=svc._recovery_until,
        recovery_position_scale=svc._RECOVERY_POSITION_SCALE,
        valid_trade_natures=svc._VALID_TRADE_NATURES,
        sub_mgr=svc._sub_mgr,
        ensure_bound_strategy=svc._ensure_bound_strategy,
        get_trading_account_id=svc._get_trading_account_id,
        extract_ai_position_pct=svc._extract_ai_position_pct,
        apply_auto_coin_position_scale=svc._apply_auto_coin_position_scale,
        append_event=svc._append_event,
        get_today_realized_pnl=svc._get_today_realized_pnl,
        get_validated_trade_nature=svc._get_validated_trade_nature,
        recover_db_session=svc._recover_db_session,
        is_unified_executor_on=svc._is_unified_executor_on,
    )


def execute_paper_trade(
    db: Session,
    session,
    strat,
    decision: dict,
    host: PaperExecutionHost,
) -> bool:
    """通过仓位管理器 + paper_engine 执行模拟下单。"""
    try:
'''

footer = '''
    except Exception as e:
        logger.error(f"[FullAuto] 模拟交易执行异常: {e}", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        host.append_event(session, "trade_error", f"{getattr(strat, 'primary_symbol', '?')}: {str(e)[:80]}")
        return False
'''

# body is indented with 12 spaces inside try; function try needs 8 spaces base
lines = body.splitlines()
dedented = []
for line in lines:
    if line.startswith("            "):
        dedented.append("        " + line[12:])
    elif line.strip() == "":
        dedented.append("")
    else:
        dedented.append(line)
body = "\n".join(dedented)

out = ROOT / "backend/services/full_auto/paper_execution.py"
out.write_text(header + body + footer, encoding="utf-8")
print(f"wrote {out} ({out.read_text(encoding='utf-8').count(chr(10))} lines)")
