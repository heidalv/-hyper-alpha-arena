# -*- coding: utf-8 -*-
"""订单级日对账（v3 方向 2，p2-oms-exec）。

仓位对账（`live_position_reconciler`）回答「我们有没有仓」；本模块回答
「每一笔订单的本地状态是否与交易所一致」。两者互补，不能互相替代：

  本地有、交易所无   → 可能发单失败却标了 filled，或被人工撤了
  交易所有、本地无   → 幽灵单（崩溃窗口 / 手工下单 / 其它程序）
  双方都有但状态不一致 → 以交易所为准强制改写，并在 recon 字段留痕

**原则**：
  - 只对 `is_ours(clientOrderId)` 的交易所订单认领；手工单单独列 orphan
  - 终态被改写必须 `reconcile=True`，并写入 `recon.forced_from/to`
  - 找不到交易所证据的 `submitted`/`unknown`：超过宽限期标 `expired`（不是 filled）
  - 默认只报告不自动修（`OMS_RECONCILE_AUTO_FIX=false`）；自动修也只改状态机，不动仓位
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.services.oms.client_id import is_ours
from backend.services.oms.order_store import (
    OrderStatus,
    TERMINAL_STATUSES,
    ensure_schema,
    get_order,
    list_orders,
    now_ms,
    transition,
)

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "oms"


def _env_true(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except Exception:
        return default


def auto_fix_enabled() -> bool:
    return _env_true("OMS_RECONCILE_AUTO_FIX", False)


def lookback_hours() -> int:
    return max(1, _env_int("OMS_RECONCILE_LOOKBACK_H", 48))


def unknown_grace_sec() -> int:
    """submitted/unknown 超过这个时间还查不到交易所证据 → 标 expired。"""
    return max(60, _env_int("OMS_RECONCILE_UNKNOWN_GRACE_SEC", 3600))


# ─────────────────────────── 交易所拉单 ───────────────────────────
def _status_from_exchange(ex_status: str, filled: float, qty: float) -> str:
    s = str(ex_status or "").lower()
    if s in ("closed", "filled"):
        return OrderStatus.FILLED if filled + 1e-12 >= qty * 0.999 else OrderStatus.PARTIAL
    if s in ("canceled", "cancelled"):
        return OrderStatus.CANCELLED if filled <= 1e-12 else OrderStatus.PARTIAL
    if s in ("rejected", "expired"):
        return OrderStatus.REJECTED if s == "rejected" else OrderStatus.EXPIRED
    if filled > 1e-12 and filled + 1e-12 < qty:
        return OrderStatus.PARTIAL
    if s in ("open", "new", "live", "partially_filled", "partial"):
        return OrderStatus.PARTIAL if filled > 1e-12 else OrderStatus.ACKED
    return OrderStatus.ACKED


async def fetch_exchange_orders(client: Any, symbol: Optional[str] = None,
                                since_ms: Optional[int] = None) -> List[Dict[str, Any]]:
    """拉交易所近期订单。返回归一化列表：
    {exchange_order_id, client_order_id, symbol, side, status, filled, qty, avg_price, ts_ms}
    """
    ex = getattr(client, "_exchange", None)
    if ex is None:
        return []
    out: List[Dict[str, Any]] = []
    since = int(since_ms or (now_ms() - lookback_hours() * 3600000))

    async def _pull(meth: str, **kw):
        fn = getattr(ex, meth, None)
        if fn is None:
            return []
        try:
            rows = await fn(**kw)
            return rows if isinstance(rows, list) else []
        except Exception as exc:
            logger.warning("[oms.reconcile] %s 失败: %s", meth, exc)
            return []

    # 优先 fetch_orders（含历史）；没有就拼 open + closed
    rows = await _pull("fetch_orders", symbol=symbol, since=since, limit=200)
    if not rows:
        open_rows = await _pull("fetch_open_orders", symbol=symbol)
        closed = await _pull("fetch_closed_orders", symbol=symbol, since=since, limit=200)
        rows = list(open_rows) + list(closed)

    for r in rows:
        if not isinstance(r, dict):
            continue
        info = r.get("info") or {}
        cid = (r.get("clientOrderId") or info.get("clientOrderId")
               or info.get("newClientOrderId") or info.get("clOrdId")
               or info.get("orderLinkId") or "")
        try:
            filled = float(r.get("filled") or 0)
            qty = float(r.get("amount") or filled or 0)
            avg = r.get("average") or r.get("price")
            avg = float(avg) if avg is not None else None
        except (TypeError, ValueError):
            filled, qty, avg = 0.0, 0.0, None
        ts = r.get("timestamp") or info.get("time") or info.get("updateTime")
        try:
            ts_ms = int(ts) if ts else None
        except (TypeError, ValueError):
            ts_ms = None
        out.append({
            "exchange_order_id": str(r.get("id") or info.get("orderId") or ""),
            "client_order_id": str(cid) if cid else None,
            "symbol": r.get("symbol") or symbol,
            "side": str(r.get("side") or "").lower(),
            "status": str(r.get("status") or ""),
            "filled": filled, "qty": qty, "avg_price": avg, "ts_ms": ts_ms,
            "raw_status": str(r.get("status") or ""),
        })
    return out


# ─────────────────────────── 比对 ───────────────────────────
def _match_key(local: Dict[str, Any], ex: Dict[str, Any]) -> bool:
    if local.get("client_order_id") and ex.get("client_order_id"):
        if str(local["client_order_id"]) == str(ex["client_order_id"]):
            return True
    if local.get("exchange_order_id") and ex.get("exchange_order_id"):
        if str(local["exchange_order_id"]) == str(ex["exchange_order_id"]):
            return True
    return False


def compare(local_orders: List[Dict[str, Any]],
            exchange_orders: List[Dict[str, Any]]) -> Dict[str, Any]:
    """纯比对（不写库）。返回 mismatches / orphans / matched。"""
    matched: List[Dict[str, Any]] = []
    mismatches: List[Dict[str, Any]] = []
    used_ex: set = set()

    for loc in local_orders:
        hit = None
        for i, ex in enumerate(exchange_orders):
            if i in used_ex:
                continue
            if _match_key(loc, ex):
                hit = ex
                used_ex.add(i)
                break
        if hit is None:
            age_sec = (now_ms() - int(loc.get("updated_ms") or loc.get("created_ms") or 0)) / 1000
            mismatches.append({
                "kind": "local_only",
                "client_order_id": loc.get("client_order_id"),
                "local_status": loc.get("status"),
                "age_sec": round(age_sec, 1),
                "symbol": loc.get("symbol"),
                "exchange": loc.get("exchange"),
            })
            continue

        want = _status_from_exchange(hit["raw_status"], hit["filled"],
                                     hit["qty"] or float(loc.get("qty") or 0))
        cur = str(loc.get("status"))
        item = {
            "client_order_id": loc.get("client_order_id"),
            "exchange_order_id": hit.get("exchange_order_id"),
            "local_status": cur, "exchange_status": want,
            "local_filled": loc.get("filled_qty"), "exchange_filled": hit.get("filled"),
            "symbol": loc.get("symbol"),
        }
        if cur == want and abs(float(loc.get("filled_qty") or 0) - float(hit.get("filled") or 0)) < 1e-8:
            matched.append(item)
        else:
            item["kind"] = "status_mismatch"
            mismatches.append(item)

    orphans: List[Dict[str, Any]] = []
    for i, ex in enumerate(exchange_orders):
        if i in used_ex:
            continue
        cid = ex.get("client_order_id")
        orphans.append({
            "kind": "exchange_only",
            "exchange_order_id": ex.get("exchange_order_id"),
            "client_order_id": cid,
            "ours": is_ours(cid),
            "status": ex.get("status"),
            "symbol": ex.get("symbol"),
            "filled": ex.get("filled"),
            "side": ex.get("side"),
        })

    return {
        "matched": matched, "mismatches": mismatches, "orphans": orphans,
        "n_local": len(local_orders), "n_exchange": len(exchange_orders),
        "n_matched": len(matched), "n_mismatch": len(mismatches),
        "n_orphan_ours": sum(1 for o in orphans if o.get("ours")),
        "n_orphan_foreign": sum(1 for o in orphans if not o.get("ours")),
    }


def apply_fixes(diff: Dict[str, Any], exchange_orders: List[Dict[str, Any]],
                *, auto_fix: bool = False) -> Dict[str, Any]:
    """按比对结果修本地状态机。默认只报告；auto_fix=True 才写。"""
    actions: List[Dict[str, Any]] = []
    grace = unknown_grace_sec()
    ex_by_cid = {str(e["client_order_id"]): e for e in exchange_orders if e.get("client_order_id")}
    ex_by_oid = {str(e["exchange_order_id"]): e for e in exchange_orders if e.get("exchange_order_id")}

    for m in diff.get("mismatches") or []:
        cid = m.get("client_order_id")
        if not cid:
            continue
        if m.get("kind") == "status_mismatch":
            want = m["exchange_status"]
            action = {"cid": cid, "action": "force_status", "to": want,
                      "from": m["local_status"]}
            if auto_fix:
                ex = ex_by_cid.get(str(cid)) or ex_by_oid.get(str(m.get("exchange_order_id") or ""))
                r = transition(
                    cid, want, reconcile=True,
                    exchange_order_id=(ex or {}).get("exchange_order_id") or m.get("exchange_order_id"),
                    filled_qty=(ex or {}).get("filled"),
                    avg_price=(ex or {}).get("avg_price"),
                    recon={"source": "daily_reconcile", "exchange_status": m["exchange_status"]},
                )
                action["ok"] = r.get("ok")
                action["reason"] = r.get("reason")
            actions.append(action)

        elif m.get("kind") == "local_only":
            loc = get_order(cid) or {}
            st = str(loc.get("status") or m.get("local_status"))
            age = float(m.get("age_sec") or 0)
            # 已是终态且交易所找不到：可能是影子单或历史清理，跳过
            if st in TERMINAL_STATUSES:
                actions.append({"cid": cid, "action": "skip_terminal_local_only", "status": st})
                continue
            # submitted/unknown 超过宽限期 → expired（不是 filled！）
            if st in (OrderStatus.SUBMITTED, OrderStatus.UNKNOWN, OrderStatus.INTENT,
                      OrderStatus.ACKED) and age >= grace:
                action = {"cid": cid, "action": "expire_unconfirmed", "age_sec": age, "from": st}
                if auto_fix:
                    r = transition(cid, OrderStatus.EXPIRED, reconcile=True,
                                   error=f"对账：交易所无证据且已超宽限期 {grace}s",
                                   recon={"source": "daily_reconcile", "reason": "local_only_expired"})
                    action["ok"] = r.get("ok")
                actions.append(action)
            else:
                actions.append({"cid": cid, "action": "wait_grace", "age_sec": age,
                                "grace_sec": grace, "status": st})

    # 我方幽灵单（交易所有 client_order_id 前缀、本地无）：记告警，不擅自建仓
    for o in diff.get("orphans") or []:
        if o.get("ours"):
            actions.append({"cid": o.get("client_order_id"), "action": "orphan_ours_alert",
                            "exchange_order_id": o.get("exchange_order_id"),
                            "symbol": o.get("symbol"), "status": o.get("status")})

    return {"auto_fix": auto_fix, "actions": actions,
            "n_actions": len(actions),
            "n_applied": sum(1 for a in actions if a.get("ok"))}


# ─────────────────────────── 入口 ───────────────────────────
async def reconcile_account(client: Any, account_id: int, exchange: str,
                            *, auto_fix: Optional[bool] = None,
                            since_ms: Optional[int] = None) -> Dict[str, Any]:
    ensure_schema()
    since = int(since_ms or (now_ms() - lookback_hours() * 3600000))
    local = list_orders(account_id=account_id, exchange=exchange, since_ms=since, limit=2000)
    # 影子单不参与对账（本来就没发到交易所）
    local = [r for r in local if not r.get("shadow")]
    try:
        exchange_orders = await fetch_exchange_orders(client, since_ms=since)
    except Exception as exc:
        return {"ok": False, "account_id": account_id, "exchange": exchange,
                "error": f"拉交易所订单失败: {exc}"}

    # 只认本账户相关的交易所订单：有我们前缀的，或能对上本地 exchange_order_id 的
    local_oids = {str(r.get("exchange_order_id")) for r in local if r.get("exchange_order_id")}
    local_cids = {str(r.get("client_order_id")) for r in local}
    filtered = []
    for e in exchange_orders:
        cid = e.get("client_order_id")
        oid = e.get("exchange_order_id")
        if (cid and (is_ours(cid) or cid in local_cids)) or (oid and oid in local_oids):
            filtered.append(e)

    diff = compare(local, filtered)
    fix = apply_fixes(diff, filtered,
                      auto_fix=auto_fix_enabled() if auto_fix is None else bool(auto_fix))
    out = {
        "ok": True, "account_id": account_id, "exchange": exchange,
        "since_ms": since, "ts_ms": now_ms(),
        "diff": {k: diff[k] for k in ("n_local", "n_exchange", "n_matched",
                                      "n_mismatch", "n_orphan_ours", "n_orphan_foreign")},
        "mismatches": diff["mismatches"][:50],
        "orphans": diff["orphans"][:50],
        "fix": fix,
    }
    _persist_latest(account_id, out)
    if diff["n_mismatch"] or diff["n_orphan_ours"]:
        logger.warning("[oms.reconcile] acct=%s ex=%s mismatch=%d orphan_ours=%d auto_fix=%s",
                       account_id, exchange, diff["n_mismatch"], diff["n_orphan_ours"],
                       fix["auto_fix"])
    return out


def _persist_latest(account_id: int, payload: Dict[str, Any]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / f"reconcile_acct{account_id}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
        latest = DATA_DIR / "reconcile_latest.json"
        latest.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                          encoding="utf-8")
    except Exception as exc:
        logger.warning("[oms.reconcile] 落盘失败: %s", exc)


def reconcile_account_sync(client: Any, account_id: int, exchange: str, **kw) -> Dict[str, Any]:
    import asyncio

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(
                    lambda: asyncio.run(reconcile_account(client, account_id, exchange, **kw))
                ).result(timeout=180)
        return loop.run_until_complete(reconcile_account(client, account_id, exchange, **kw))
    except RuntimeError:
        return asyncio.run(reconcile_account(client, account_id, exchange, **kw))


def latest_report(account_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    try:
        path = (DATA_DIR / f"reconcile_acct{account_id}.json") if account_id is not None \
            else (DATA_DIR / "reconcile_latest.json")
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
