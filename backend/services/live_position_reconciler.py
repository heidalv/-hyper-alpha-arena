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


# ── 外部（手工）持仓识别 ─────────────────────────────────────────

_EXTERNAL_PREFIX = "ext:"


def _external_symbols_env() -> set:
    """``LIVE_RECONCILE_EXTERNAL_SYMBOLS``：显式声明为手工持仓的符号（逗号分隔）。

    自动判据（见 ``_is_external_position``）已能覆盖绝大多数情况，此开关用于
    人工兜底：万一系统曾经在该符号上开过仓、账本残留导致自动判据失效，可在
    ``.env`` 里显式点名，避免继续刷告警。
    """
    raw = os.getenv("LIVE_RECONCILE_EXTERNAL_SYMBOLS", "") or ""
    return {s.strip().upper() for s in raw.split(",") if s.strip()}


def _is_external_position(symbol: str, rec: dict) -> bool:
    """判定某个不一致的持仓是否为「外部手工开仓」。

    [2026-09-02 因子闭环修复 D12] 背景：账户 188 的 VIRTUAL / XPL 为手工开仓，
    本地账本没有对应子仓，对账连续 86 轮报 ERROR + CRITICAL 刷屏，把真正需要
    关注的账本漂移淹没在噪音里；而 ``_align_ledger_to_exchange`` 对这种情况本来
    就只会返回 ``no_local_subs``（拒绝臆造），告警既无法处置也无人可处置。

    判据：**本地账本净仓为 0，而交易所有仓**。LPM 账本是系统持仓的唯一定义，
    账本认为自己没仓时，交易所那笔仓必然来自系统之外（手工下单/其他程序）。

    刻意不采用「历史上从未有过该符号的子仓记录」这一更严判据：用户手工操作的
    符号往往正是系统交易过的热门符号（VIRTUAL 就是），历史 closed 记录会让严判据
    失效，问题照旧。代价是账本因故障丢失 open 行时，真实漂移会被降级为 external
    —— 故此处仍完整记录到状态文件并在健康面板单列展示（只降噪、不隐藏）。

    注意与自动对齐的 ``close_all`` 场景（本地有仓、交易所 flat）方向正好相反，
    两者不会互相干扰。
    """
    if str(symbol or "").upper() in _external_symbols_env():
        return True
    try:
        _local = abs(float(rec.get("local") or 0.0))
        _exch = abs(float(rec.get("exchange") or 0.0))
    except (TypeError, ValueError):
        return False
    return _local < 1e-8 and _exch > 1e-8


def get_external_positions() -> List[Dict[str, Any]]:
    """读取当前已识别的外部手工持仓（供健康面板单独展示）。"""
    out: List[Dict[str, Any]] = []
    for key, val in (_load_state() or {}).items():
        if not key.startswith(_EXTERNAL_PREFIX) or not isinstance(val, dict):
            continue
        out.append({
            "account_id": val.get("account_id"),
            "symbol": val.get("symbol"),
            "exchange_qty": val.get("qty"),
            "first_seen": val.get("first_seen"),
            "last_seen": val.get("last_seen"),
            "source": val.get("source", "auto"),
        })
    return sorted(out, key=lambda r: (str(r.get("symbol") or "")))


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
        # [2026-09-02 D12] 跨日只重置当日 mismatch 计数；外部手工持仓的登记是
        # 持续状态（面板要显示这笔仓挂了多久），保留才能留住 first_seen。
        _keep_ext = {
            k: v for k, v in state.items() if k.startswith(_EXTERNAL_PREFIX)
        }
        state = {"day": day, **_keep_ext}
    key_prefix = f"a{account_id}:"
    # 清理本账户旧 key（跨日或符号消失）。external 键同样清理：符号从交易所
    # 消失说明手工仓已被平掉，登记不应长期残留在健康面板上。
    _live_syms = {str(p.get("symbol") or "") for p in positions}
    for k in list(state.keys()):
        if k == "day":
            continue
        _bare = k[len(_EXTERNAL_PREFIX):] if k.startswith(_EXTERNAL_PREFIX) else k
        if _bare.startswith(key_prefix):
            if _bare[len(key_prefix):] not in _live_syms:
                state.pop(k, None)

    seen = set()
    results = []
    for p in positions:
        sym = str(p.get("symbol") or "")
        if not sym:
            continue
        seen.add(sym)
        # [2026-08-29 对账致盲修复] LiveExecutor.get_positions 返回字段是
        # size(无符号)+side；此前只读 net_qty/qty → 恒为 0 → 永远"对账一致"，
        # 交易所真实持仓（如 a188 VIRTUAL 333）从未被识别为 mismatch，
        # 孤儿/手动仓位对系统完全不可见。
        qty = float(p.get("net_qty") or p.get("qty") or 0.0)
        if abs(qty) < 1e-12:
            _sz = float(p.get("size") or 0.0)
            if _sz > 0:
                qty = -_sz if str(p.get("side") or "").lower() == "short" else _sz
        lev = float(p.get("leverage") or 1.0)
        key = f"{key_prefix}{sym}"
        # log_mismatch=False：不一致的分级判定（外部手工持仓 INFO / 真漂移 ERROR）
        # 由下面几行统一负责，交给 LPM 一律 WARNING 会把两者混成同一种噪音。
        rec = lpm.reconcile(db, account_id, sym, qty, lev, log_mismatch=False)
        if rec["matched"]:
            if state.get(key, {}).get("rounds", 0) > 0:
                logger.info("[LiveReconciler] %s 对账恢复一致", key)
            state[key] = {"rounds": 0, "diff": 0.0}
            results.append({"symbol": sym, "matched": True})
            continue

        # [2026-09-02 D12] 外部手工持仓：从 MISMATCH 判定中摘除。
        # 系统不接管、不自动对齐、不纳入盈亏统计，只做登记；日志降为 INFO 且
        # 仅在数量变化时打印（此前每轮 ERROR+CRITICAL，86 轮噪音把真实漂移淹没）。
        if _is_external_position(sym, rec):
            _ext_key = f"{_EXTERNAL_PREFIX}{key}"
            _prev = state.get(_ext_key) if isinstance(state.get(_ext_key), dict) else None
            _prev_qty = float((_prev or {}).get("qty") or 0.0)
            _now_iso = time.strftime("%Y-%m-%d %H:%M:%S")
            state[_ext_key] = {
                "account_id": int(account_id),
                "symbol": sym,
                "qty": float(qty),
                "first_seen": (_prev or {}).get("first_seen") or _now_iso,
                "last_seen": _now_iso,
                "source": (
                    "env" if sym.upper() in _external_symbols_env() else "auto"
                ),
            }
            # 该符号不再算 mismatch，清掉可能残留的告警计数
            state.pop(key, None)
            if _prev is None or abs(_prev_qty - float(qty)) > 1e-8:
                logger.info(
                    "[LiveReconciler] 外部手工持仓 %s exchange=%.6f（本地无账本，"
                    "系统不接管；%s）",
                    key, qty,
                    "首次识别" if _prev is None else f"数量变化 {_prev_qty:+.6f}→{qty:+.6f}",
                )
            results.append({"symbol": sym, "matched": True, "external": True,
                            "exchange_qty": float(qty)})
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
    from backend.database.connection import SessionLocal, release_idle_txn
    from backend.database.models import Account
    from backend.services.exchange.live_executor import LiveExecutor

    db = SessionLocal()
    try:
        accounts = db.query(Account).filter(Account.trading_mode == "live").all()
        account_ids = [int(a.id) for a in accounts]
        # [2026-09-07] 账户列表读完即结束事务：下方 get_positions 是交易所网络，
        # 旧实现整段 idle-in-transaction → LeakGuard 点名 reconcile_all_live_accounts。
        release_idle_txn(db, where="reconcile_all.pre_exchange")
        if not account_ids:
            return {"ok": True, "accounts": 0, "note": "no live accounts"}
        executor = LiveExecutor()
        out = []
        for aid in account_ids:
            try:
                r = run_reconcile_once(
                    db, aid,
                    lambda aid: executor.get_positions(db, aid),
                )
                out.append({"account_id": aid, **r})
                release_idle_txn(db, where=f"reconcile_all.post:{aid}")
            except Exception as e:
                logger.exception("[LiveReconciler] account=%s 异常: %s", aid, e)
                try:
                    db.rollback()
                except Exception:
                    pass
        return {"ok": True, "accounts": len(out), "results": out}
    finally:
        db.close()
