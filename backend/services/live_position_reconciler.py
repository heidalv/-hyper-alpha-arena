"""方案2 P2：周期对账任务（本地虚拟子仓 Σ vs 交易所实仓）。

- run_reconcile_once(db, account_id, fetch_positions_fn)：
  对每个有实仓的 symbol 调 LPM.reconcile；mismatch 记入状态文件并累计轮次，
  首轮告警(ERROR)，连续 >= LIVE_RECONCILE_AUTO_FIX_ROUNDS(默认2) 且
  LIVE_RECONCILE_AUTO_FIX=true 时自动对齐（以交易所实仓为准回写本地账本）。
- reconcile_all_live_accounts()：遍历 live 账户，经 LiveExecutor.get_positions
  取实仓，逐个对账。无 live 账户时 no-op（纸面盘零影响）。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_STATE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "data",
    "live_reconcile_state.json",
)


def _load_state() -> Dict[str, Any]:
    try:
        if os.path.exists(_STATE_PATH):
            with open(_STATE_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}


def _save_state(state: Dict[str, Any]) -> None:
    try:
        with open(_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
    except Exception as e:
        logger.warning("[LiveReconciler] 状态落盘失败: %s", e)


def _auto_fix_enabled() -> bool:
    return os.getenv("LIVE_RECONCILE_AUTO_FIX", "true").strip().lower() in (
        "true", "1", "yes", "on",
    )


def _auto_fix_rounds() -> int:
    try:
        return max(1, int(os.getenv("LIVE_RECONCILE_AUTO_FIX_ROUNDS", "2") or 2))
    except (TypeError, ValueError):
        return 2


def _align_ledger_to_exchange(db, account_id: int, symbol: str,
                              exchange_qty: float) -> dict:
    """以交易所实仓为准回写本地账本（自动对齐策略）。

    - 交易所 flat → 本地全部子仓关平；
    - 交易所净仓与本地同向 → 按比例缩放各子仓 size（保持分层结构）；
    - 本地无子仓或方向冲突 → 不臆造，返回需要人工介入。
    """
    from backend.database.models import LiveSubPosition

    subs = db.query(LiveSubPosition).filter(
        LiveSubPosition.account_id == account_id,
        LiveSubPosition.symbol == symbol,
        LiveSubPosition.status == "open",
    ).all()

    if abs(exchange_qty) < 1e-8:
        for p in subs:
            p.status = "closed"
            p.size = 0.0
            p.margin = 0.0
        db.commit()
        return {"fixed": True, "mode": "close_all", "subs": len(subs)}

    local_qty = sum((p.size if p.side == "long" else -p.size) for p in subs)
    if abs(local_qty) < 1e-8:
        return {"fixed": False, "mode": "no_local_subs", "reason": "本地无子仓但交易所非零, 需人工"}

    # 方向冲突（本地多 vs 交易所空）：不缩放，需人工
    if (local_qty > 0) != (exchange_qty > 0):
        return {"fixed": False, "mode": "side_conflict",
                "reason": f"本地{local_qty:+.6f} vs 交易所{exchange_qty:+.6f} 方向相反, 需人工"}

    factor = exchange_qty / local_qty
    for p in subs:
        p.size = round(p.size * factor, 8)
        p.margin = p.size / max(p.leverage, 1.0)
    db.commit()
    return {"fixed": True, "mode": "scale", "factor": round(factor, 6), "subs": len(subs)}


def run_reconcile_once(
    db,
    account_id: int,
    fetch_positions_fn: Callable[[int], List[Dict[str, Any]]],
) -> dict:
    """单轮对账。fetch_positions_fn(account_id) ->
    [{symbol, net_qty(signed), leverage}]（交易所口径）。"""
    from backend.services.live_position_manager import live_position_manager as lpm

    try:
        positions = fetch_positions_fn(account_id) or []
    except Exception as e:
        logger.error("[LiveReconciler] 实仓拉取失败 account=%s: %s", account_id, e)
        return {"ok": False, "error": f"fetch_failed:{type(e).__name__}"}

    state = _load_state()
    day = time.strftime("%Y-%m-%d")
    if state.get("day") != day:
        state = {"day": day}
    key_prefix = f"a{account_id}:"
    # 清理本账户旧 key（跨日或符号消失）
    for k in list(state.keys()):
        if k.startswith(key_prefix) and k != "day":
            if not any(f"{key_prefix}{p.get('symbol')}" == k for p in positions):
                state.pop(k, None)

    seen = set()
    results = []
    for p in positions:
        sym = str(p.get("symbol") or "")
        if not sym:
            continue
        seen.add(sym)
        qty = float(p.get("net_qty") or p.get("qty") or 0.0)
        lev = float(p.get("leverage") or 1.0)
        key = f"{key_prefix}{sym}"
        rec = lpm.reconcile(db, account_id, sym, qty, lev)
        if rec["matched"]:
            if state.get(key, {}).get("rounds", 0) > 0:
                logger.info("[LiveReconciler] %s 对账恢复一致", key)
            state[key] = {"rounds": 0, "diff": 0.0}
            results.append({"symbol": sym, "matched": True})
            continue

        rounds = int(state.get(key, {}).get("rounds", 0)) + 1
        state[key] = {"rounds": rounds, "diff": float(rec["diff"])}
        logger.error(
            "[LiveReconciler] MISMATCH %s local=%.6f exchange=%.6f diff=%.6f rounds=%d",
            key, rec["local"], rec["exchange"], rec["diff"], rounds,
        )
        fix = None
        if _auto_fix_enabled() and rounds >= _auto_fix_rounds():
            try:
                fix = _align_ledger_to_exchange(db, account_id, sym, qty)
                if fix.get("fixed"):
                    state[key] = {"rounds": 0, "diff": 0.0}
                    logger.warning(
                        "[LiveReconciler] 自动对齐 %s: %s", key, fix,
                    )
                else:
                    logger.critical(
                        "[LiveReconciler] 自动对齐失败需人工 %s: %s", key, fix,
                    )
            except Exception as e:
                logger.exception("[LiveReconciler] 自动对齐异常 %s: %s", key, e)
        results.append({"symbol": sym, "matched": False,
                        "rounds": rounds, "auto_fix": fix})
    _save_state(state)
    return {"ok": True, "account_id": account_id, "results": results}


def reconcile_all_live_accounts() -> dict:
    """遍历 live 账户对账（调度任务入口）。无 live 账户 no-op。"""
    from backend.database.connection import SessionLocal
    from backend.database.models import Account
    from backend.services.exchange.live_executor import LiveExecutor

    db = SessionLocal()
    try:
        accounts = db.query(Account).filter(Account.trading_mode == "live").all()
        if not accounts:
            return {"ok": True, "accounts": 0, "note": "no live accounts"}
        executor = LiveExecutor()
        out = []
        for acc in accounts:
            try:
                r = run_reconcile_once(
                    db, acc.id,
                    lambda aid: executor.get_positions(db, aid),
                )
                out.append({"account_id": acc.id, **r})
            except Exception as e:
                logger.exception("[LiveReconciler] account=%s 异常: %s", acc.id, e)
        return {"ok": True, "accounts": len(out), "results": out}
    finally:
        db.close()
