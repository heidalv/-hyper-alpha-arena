# -*- coding: utf-8 -*-
"""`live_orders` 表与订单状态机（v3 方向 2，p2-oms-exec）。

**唯一的状态写入口**：所有状态变更必须走 `transition()`，它会校验转移合法性。
散落各处直接 UPDATE 是订单系统最常见的腐化源——一旦有人从终态改回活跃态，
对账就永远对不平，而且没人知道是谁改的。

状态机：

    intent ──submit──> submitted ──ack──> acked ──┬─> partial ──> filled
       │                   │                      ├─> filled
       │                   │                      ├─> cancelled
       └──> rejected       └──> rejected          └─> expired
                           └──> unknown（发出后失联，等对账救回）

关键设计：
  - **intent 先落库再下单**。拿到 client_order_id 才发单，网络超时可以安全重试
    （交易所按 client_order_id 去重）。反过来「先下单再记录」永远存在
    「单发出去了但没记上」的窗口，崩溃后就成了幽灵仓位。
  - **unknown 是一等状态**。发单后连不上交易所时不能猜 —— 既不能当成功
    （可能没成交）也不能当失败（可能已成交），只能标 unknown 交给对账查证。
  - 终态不可变，除非 `reconcile=True`（对账以交易所为准修正），且必须留痕。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

_schema_ready = False


# ─────────────────────────── 状态定义 ───────────────────────────
class OrderStatus:
    INTENT = "intent"          # 已落库，未发出
    SUBMITTED = "submitted"    # 已发出，未收到交易所确认
    ACKED = "acked"            # 交易所已受理（有 exchange_order_id）
    PARTIAL = "partial"        # 部分成交
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNKNOWN = "unknown"        # 发出后失联，需对账查证


TERMINAL_STATUSES = frozenset({
    OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED,
})
ACTIVE_STATUSES = frozenset({
    OrderStatus.INTENT, OrderStatus.SUBMITTED, OrderStatus.ACKED,
    OrderStatus.PARTIAL, OrderStatus.UNKNOWN,
})

# 合法转移。缺省不允许 —— 白名单而非黑名单，新状态必须显式接进来。
_ALLOWED: Dict[str, frozenset] = {
    OrderStatus.INTENT: frozenset({OrderStatus.SUBMITTED, OrderStatus.REJECTED,
                                   OrderStatus.CANCELLED}),
    OrderStatus.SUBMITTED: frozenset({OrderStatus.ACKED, OrderStatus.PARTIAL, OrderStatus.FILLED,
                                      OrderStatus.REJECTED, OrderStatus.CANCELLED,
                                      OrderStatus.EXPIRED, OrderStatus.UNKNOWN}),
    OrderStatus.ACKED: frozenset({OrderStatus.PARTIAL, OrderStatus.FILLED, OrderStatus.CANCELLED,
                                  OrderStatus.EXPIRED, OrderStatus.REJECTED, OrderStatus.UNKNOWN}),
    OrderStatus.PARTIAL: frozenset({OrderStatus.PARTIAL, OrderStatus.FILLED, OrderStatus.CANCELLED,
                                    OrderStatus.EXPIRED, OrderStatus.UNKNOWN}),
    OrderStatus.UNKNOWN: frozenset({OrderStatus.ACKED, OrderStatus.PARTIAL, OrderStatus.FILLED,
                                    OrderStatus.CANCELLED, OrderStatus.REJECTED,
                                    OrderStatus.EXPIRED}),
    # 终态：只有对账能改（transition(..., reconcile=True)）
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
}


def now_ms() -> int:
    return int(time.time() * 1000)


def _db():
    from backend.database.connection import SessionLocal

    return SessionLocal()


def _json(obj: Any) -> Optional[str]:
    if obj is None:
        return None
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return None


def _loads(v: Any) -> Any:
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return None


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    from sqlalchemy import text

    stmts = [
        """
        CREATE TABLE IF NOT EXISTS live_orders (
            client_order_id VARCHAR(40) PRIMARY KEY,
            parent_id VARCHAR(40),
            account_id INTEGER NOT NULL,
            exchange VARCHAR(24) NOT NULL,
            symbol VARCHAR(40) NOT NULL,
            side VARCHAR(8) NOT NULL,
            order_type VARCHAR(16) NOT NULL,
            qty DOUBLE PRECISION NOT NULL,
            price DOUBLE PRECISION,
            reduce_only BOOLEAN DEFAULT FALSE,
            post_only BOOLEAN DEFAULT FALSE,
            leverage INTEGER DEFAULT 1,
            status VARCHAR(16) NOT NULL,
            exchange_order_id VARCHAR(64),
            filled_qty DOUBLE PRECISION DEFAULT 0,
            avg_price DOUBLE PRECISION,
            fee DOUBLE PRECISION,
            created_ms BIGINT NOT NULL,
            submitted_ms BIGINT,
            acked_ms BIGINT,
            terminal_ms BIGINT,
            updated_ms BIGINT NOT NULL,
            strategy_id VARCHAR(64),
            chase_seq INTEGER DEFAULT 0,
            shadow BOOLEAN DEFAULT FALSE,
            intent JSONB,
            recon JSONB,
            error TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_live_orders_status ON live_orders (status, updated_ms DESC)",
        "CREATE INDEX IF NOT EXISTS idx_live_orders_acct_created ON live_orders (account_id, created_ms DESC)",
        "CREATE INDEX IF NOT EXISTS idx_live_orders_exchange_oid ON live_orders (exchange, exchange_order_id)",
        "CREATE INDEX IF NOT EXISTS idx_live_orders_parent ON live_orders (parent_id)",
    ]
    db = _db()
    try:
        for s in stmts:
            db.execute(text(s))
        db.commit()
        _schema_ready = True
    except Exception as exc:
        db.rollback()
        logger.warning("[oms.store] 建表失败: %s", exc)
    finally:
        db.close()


# ─────────────────────────── 写入 ───────────────────────────
def record_intent(
    *,
    client_order_id: str,
    account_id: int,
    exchange: str,
    symbol: str,
    side: str,
    order_type: str,
    qty: float,
    price: Optional[float] = None,
    reduce_only: bool = False,
    post_only: bool = False,
    leverage: int = 1,
    strategy_id: Optional[str] = None,
    parent_id: Optional[str] = None,
    chase_seq: int = 0,
    shadow: bool = False,
    intent: Optional[Dict[str, Any]] = None,
) -> bool:
    """落一条 `intent` 记录。**必须在真正下单之前调用**。

    返回 False 表示落库失败 —— 此时调用方**绝不能下单**，否则就是没有账本的裸单。
    """
    ensure_schema()
    from sqlalchemy import text

    db = _db()
    try:
        db.execute(
            text(
                """
                INSERT INTO live_orders (client_order_id, parent_id, account_id, exchange, symbol,
                    side, order_type, qty, price, reduce_only, post_only, leverage, status,
                    created_ms, updated_ms, strategy_id, chase_seq, shadow, intent)
                VALUES (:cid, :pid, :acct, :ex, :sym, :side, :otype, :qty, :px, :ro, :po, :lev,
                    'intent', :now, :now, :sid, :seq, :shadow, CAST(:intent AS JSONB))
                ON CONFLICT (client_order_id) DO NOTHING
                """
            ),
            {"cid": client_order_id, "pid": parent_id, "acct": int(account_id),
             "ex": str(exchange)[:24], "sym": str(symbol)[:40], "side": str(side)[:8],
             "otype": str(order_type)[:16], "qty": float(qty), "px": price,
             "ro": bool(reduce_only), "po": bool(post_only), "lev": int(leverage or 1),
             "now": now_ms(), "sid": (strategy_id or "")[:64], "seq": int(chase_seq),
             "shadow": bool(shadow), "intent": _json(intent or {})},
        )
        db.commit()
        return True
    except Exception as exc:
        db.rollback()
        logger.error("[oms.store] intent 落库失败 cid=%s: %s", client_order_id, exc)
        return False
    finally:
        db.close()


def transition(
    client_order_id: str,
    to_status: str,
    *,
    exchange_order_id: Optional[str] = None,
    filled_qty: Optional[float] = None,
    avg_price: Optional[float] = None,
    fee: Optional[float] = None,
    error: Optional[str] = None,
    recon: Optional[Dict[str, Any]] = None,
    reconcile: bool = False,
) -> Dict[str, Any]:
    """状态机唯一入口。返回 `{ok, from, to, reason}`。

    `reconcile=True` 时允许覆盖终态（对账以交易所为准），并把原状态记进 `recon`
    —— 终态被改写是需要人看一眼的事，不能悄悄发生。
    """
    ensure_schema()
    from sqlalchemy import text

    if to_status not in _ALLOWED and to_status not in TERMINAL_STATUSES:
        return {"ok": False, "reason": f"未知状态 {to_status}"}

    db = _db()
    try:
        row = db.execute(
            text("SELECT status, filled_qty, recon FROM live_orders WHERE client_order_id = :cid"),
            {"cid": client_order_id},
        ).first()
        if not row:
            return {"ok": False, "reason": "订单不存在"}
        cur = str(row[0])
        if cur == to_status and to_status != OrderStatus.PARTIAL:
            return {"ok": True, "from": cur, "to": to_status, "reason": "状态未变"}

        allowed = _ALLOWED.get(cur, frozenset())
        if to_status not in allowed:
            if not reconcile:
                logger.warning("[oms.store] 非法状态转移 %s: %s → %s", client_order_id, cur, to_status)
                return {"ok": False, "from": cur, "to": to_status,
                        "reason": f"不允许从 {cur} 转到 {to_status}"}
            logger.warning("[oms.store] 对账强制改写 %s: %s → %s", client_order_id, cur, to_status)

        sets = ["status = :st", "updated_ms = :now"]
        params: Dict[str, Any] = {"cid": client_order_id, "st": to_status, "now": now_ms()}
        if exchange_order_id is not None:
            sets.append("exchange_order_id = :eoid")
            params["eoid"] = str(exchange_order_id)[:64]
        if filled_qty is not None:
            sets.append("filled_qty = :fq")
            params["fq"] = float(filled_qty)
        if avg_price is not None:
            sets.append("avg_price = :ap")
            params["ap"] = float(avg_price)
        if fee is not None:
            sets.append("fee = :fee")
            params["fee"] = float(fee)
        if error is not None:
            sets.append("error = :err")
            params["err"] = str(error)[:800]
        if to_status == OrderStatus.SUBMITTED:
            sets.append("submitted_ms = :now")
        if to_status == OrderStatus.ACKED:
            sets.append("acked_ms = COALESCE(acked_ms, :now)")
        if to_status in TERMINAL_STATUSES:
            sets.append("terminal_ms = :now")

        merged_recon = _loads(row[2]) or {}
        if reconcile:
            merged_recon = {**merged_recon,
                            "forced_from": cur, "forced_to": to_status, "forced_ms": now_ms()}
        if recon:
            merged_recon = {**merged_recon, **recon}
        if merged_recon:
            sets.append("recon = CAST(:recon AS JSONB)")
            params["recon"] = _json(merged_recon)

        db.execute(text(f"UPDATE live_orders SET {', '.join(sets)} WHERE client_order_id = :cid"),
                   params)
        db.commit()
        return {"ok": True, "from": cur, "to": to_status}
    except Exception as exc:
        db.rollback()
        logger.error("[oms.store] 状态转移失败 %s → %s: %s", client_order_id, to_status, exc)
        return {"ok": False, "reason": str(exc)[:200]}
    finally:
        db.close()


# ─────────────────────────── 读取 ───────────────────────────
def _row_to_dict(r: Any) -> Dict[str, Any]:
    d = dict(r)
    for k in ("intent", "recon"):
        d[k] = _loads(d.get(k))
    return d


def get_order(client_order_id: str) -> Optional[Dict[str, Any]]:
    ensure_schema()
    from sqlalchemy import text

    db = _db()
    try:
        r = db.execute(text("SELECT * FROM live_orders WHERE client_order_id = :cid"),
                       {"cid": client_order_id}).mappings().first()
        return _row_to_dict(r) if r else None
    except Exception as exc:
        logger.warning("[oms.store] 查询失败: %s", exc)
        return None
    finally:
        db.close()


def list_orders(
    *,
    account_id: Optional[int] = None,
    exchange: Optional[str] = None,
    symbol: Optional[str] = None,
    statuses: Optional[Sequence[str]] = None,
    since_ms: Optional[int] = None,
    until_ms: Optional[int] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    ensure_schema()
    from sqlalchemy import bindparam, text

    where, params = ["1=1"], {"lim": max(1, min(int(limit), 5000))}
    if account_id is not None:
        where.append("account_id = :acct")
        params["acct"] = int(account_id)
    if exchange:
        where.append("exchange = :ex")
        params["ex"] = str(exchange)
    if symbol:
        where.append("symbol = :sym")
        params["sym"] = str(symbol)
    if since_ms is not None:
        where.append("created_ms >= :lo")
        params["lo"] = int(since_ms)
    if until_ms is not None:
        where.append("created_ms <= :hi")
        params["hi"] = int(until_ms)
    expanding = False
    if statuses:
        where.append("status IN :sts")
        params["sts"] = list(statuses)
        expanding = True

    db = _db()
    try:
        stmt = text(f"SELECT * FROM live_orders WHERE {' AND '.join(where)} "
                    f"ORDER BY created_ms DESC LIMIT :lim")
        if expanding:
            stmt = stmt.bindparams(bindparam("sts", expanding=True))
        rows = db.execute(stmt, params).mappings().all()
        return [_row_to_dict(r) for r in rows]
    except Exception as exc:
        logger.warning("[oms.store] 列表查询失败: %s", exc)
        return []
    finally:
        db.close()


def open_orders(*, account_id: Optional[int] = None, max_age_h: float = 72.0) -> List[Dict[str, Any]]:
    """仍处于活跃态的订单。重启后用它捞回悬挂单。"""
    return list_orders(account_id=account_id, statuses=sorted(ACTIVE_STATUSES),
                       since_ms=now_ms() - int(max_age_h * 3600 * 1000), limit=2000)


def stuck_orders(*, older_than_sec: float = 300.0) -> List[Dict[str, Any]]:
    """卡在非终态太久的订单 —— 这些是最危险的：可能单已发出但本地不知道结果。"""
    cutoff = now_ms() - int(older_than_sec * 1000)
    rows = list_orders(statuses=[OrderStatus.INTENT, OrderStatus.SUBMITTED, OrderStatus.UNKNOWN],
                       limit=2000)
    return [r for r in rows if int(r.get("updated_ms") or 0) < cutoff]


def stats(days: int = 1) -> Dict[str, Any]:
    """看板用：窗口内订单量、状态分布、maker 占比、平均成交率。"""
    ensure_schema()
    from sqlalchemy import text

    since = now_ms() - int(days) * 86400000
    db = _db()
    try:
        rows = db.execute(text(
            "SELECT status, COUNT(*) AS n, SUM(CASE WHEN post_only THEN 1 ELSE 0 END) AS n_maker, "
            "SUM(COALESCE(filled_qty,0)) AS filled, SUM(qty) AS total "
            "FROM live_orders WHERE created_ms >= :lo GROUP BY status"
        ), {"lo": since}).mappings().all()
        by_status = {str(r["status"]): int(r["n"]) for r in rows}
        n = sum(by_status.values())
        n_maker = sum(int(r["n_maker"] or 0) for r in rows)
        filled = sum(float(r["filled"] or 0) for r in rows)
        total = sum(float(r["total"] or 0) for r in rows)
        return {
            "days": days, "n_orders": n, "by_status": by_status,
            "maker_ratio": round(n_maker / n, 4) if n else None,
            "fill_ratio": round(filled / total, 4) if total > 0 else None,
            "n_terminal": sum(by_status.get(s, 0) for s in TERMINAL_STATUSES),
            "n_active": sum(by_status.get(s, 0) for s in ACTIVE_STATUSES),
        }
    except Exception as exc:
        logger.warning("[oms.store] 统计失败: %s", exc)
        return {"days": days, "error": str(exc)[:200]}
    finally:
        db.close()


def shadow_mode() -> bool:
    """影子模式：落库但不真发单。默认开 —— 接管实盘必须是显式动作。"""
    raw = os.getenv("OMS_SHADOW")
    if raw is None:
        return True
    return str(raw).strip().lower() in ("1", "true", "yes", "on")
