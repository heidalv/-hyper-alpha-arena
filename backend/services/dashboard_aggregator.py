"""统一账户概览聚合服务 — 交易矩阵仪表盘

新版可配置仪表盘的单一数据聚合入口。按 `trading_mode` 分流，复用现有服务的计算逻辑，
不重新实现任何盈亏/胜率算法：

    paper              -> backend.services.paper_trading_engine.paper_engine
    testnet / mainnet  -> backend.api.arena_routes._get_hyperliquid_positions
                          + backend.services.hyperliquid_environment.get_hyperliquid_client（win_rate）

对外只暴露 `get_account_overview()` / `get_accounts_overview()`，返回统一 schema，
供 `/api/dashboard/overview` 直接序列化。这是纯只读聚合，不触碰下单/资金逻辑。
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── 轻量 TTL 缓存：Hyperliquid trading-stats 是实时 API 调用（无内置缓存），
#    多账户仪表盘轮询很容易在短时间内重复触发；这里用进程内缓存兜底，避免打爆交易所 API。
_stats_cache: Dict[str, Dict[str, Any]] = {}
_STATS_TTL_SECONDS = 30.0

# ── 整条 overview 结果的 TTL 缓存 ──
# get_balance/get_positions 内部会对每个持仓做一次实时行情查询（未命中本地价格缓存时
# 会兜底到交易所 REST，单次可能耗时数秒）。仪表盘同时存在 HTTP 轮询 + 多 widget 触发 +
# WS snapshot 变化信号触发刷新，短时间内极易对同一账户并发/重复调用，互相排队导致页面
# 整体卡顿。这里按账户+模式做短 TTL 缓存，命中时直接复用，不再重新查价。
_overview_cache: Dict[str, Dict[str, Any]] = {}
_OVERVIEW_TTL_SECONDS = 8.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _get_cached_trading_stats(db: Session, account_id: int, environment: str) -> Dict[str, Any]:
    cache_key = f"{account_id}:{environment}"
    now = time.time()
    cached = _stats_cache.get(cache_key)
    if cached and (now - cached["ts"]) < _STATS_TTL_SECONDS:
        return cached["data"]

    stats: Dict[str, Any] = {}
    try:
        from backend.services.hyperliquid_environment import get_hyperliquid_client
        client = get_hyperliquid_client(db, account_id, override_environment=environment)
        if client:
            stats = client.get_trading_stats(db) or {}
    except Exception as exc:
        logger.debug(f"[DashboardAggregator] trading-stats 获取失败 account={account_id} env={environment}: {exc}")
        stats = {}

    _stats_cache[cache_key] = {"ts": now, "data": stats}
    return stats


def _empty_overview(account_id: int, exchange: str, trading_mode: str) -> Dict[str, Any]:
    return {
        "account_id": account_id,
        "exchange": exchange,
        "trading_mode": trading_mode,
        "account_name": None,
        "equity": 0.0,
        "available_cash": 0.0,
        "unrealized_pnl": 0.0,
        "realized_pnl": 0.0,
        "total_pnl": 0.0,
        "win_rate": 0.0,
        "total_trades": 0,
        "active_positions": 0,
        "positions": [],
        "error": None,
        "updated_at": _now_iso(),
    }


def _fill_paper_overview(db: Session, account_id: int, base: Dict[str, Any]) -> None:
    from backend.services.paper_trading_engine import paper_engine
    from backend.database.models import Account, PaperBalance

    # [2026-07-09 性能修复] 用独立短生命周期 session，避免 get_positions 内对每个持仓
    # 逐个查实时行情（未命中缓存时兜底 REST，单持仓数秒）期间，请求主 session 的事务
    # 一直 "idle in transaction" 占着锁（LeakGuard 日志 age 高达数十秒）。独立 session
    # 在 finally 中关闭，事务立即释放，不与其他读操作争锁。
    from backend.database.connection import SessionLocal
    paper_db = SessionLocal()
    try:
        account = paper_db.query(Account).filter(Account.id == account_id).first()
        if account:
            base["account_name"] = account.name

        # 性能注意：get_balance() 内部会对每个持仓单独查一次实时行情（未命中缓存时兜底
        # 到交易所 REST，单次可能耗时数秒），而 get_positions() 也会各自重复查一次同样的
        # 行情。原实现两者都调用 = 同一批持仓被查价两次。这里只调用一次 get_positions()
        # （拿到刷新后的 mark_price/unrealized_pnl），再直接读 PaperBalance 表的现金字段
        # 自行算出权益，避免重复的网络往返。
        positions = paper_engine.get_positions(paper_db, account_id, status="open") or []
        base["positions"] = [
            {
                "symbol": p.get("symbol"),
                "side": p.get("side"),
                "size": p.get("size"),
                "entry_price": p.get("entry_price"),
                "mark_price": p.get("mark_price"),
                "unrealized_pnl": p.get("unrealized_pnl"),
                "leverage": p.get("leverage"),
            }
            for p in positions
        ]
        base["active_positions"] = len(positions)

        unrealized_total = sum(float(p.get("unrealized_pnl") or 0.0) for p in positions)
        bal = paper_db.query(PaperBalance).filter(PaperBalance.account_id == account_id).first()
        if bal:
            base["available_cash"] = round(bal.available_balance, 2)
            base["realized_pnl"] = round(bal.realized_pnl, 2)
            base["equity"] = round(bal.available_balance + bal.frozen_margin + unrealized_total, 2)
        base["unrealized_pnl"] = round(unrealized_total, 2)

        summary = paper_engine.get_summary(paper_db, account_id) or {}
        win_rate = summary.get("win_rate")
        base["win_rate"] = round(float(win_rate) * 100, 2) if win_rate is not None else 0.0
        base["total_trades"] = summary.get("total_orders", 0)
        base["total_pnl"] = summary.get(
            "total_pnl", round(base["unrealized_pnl"] + base["realized_pnl"], 2)
        )
    finally:
        paper_db.close()


def _fill_hyperliquid_overview(db: Session, account_id: int, environment: str, base: Dict[str, Any]) -> None:
    from backend.api.arena_routes import _get_hyperliquid_positions

    # [2026-07-09 性能修复] 用独立短生命周期 session，避免 REST 调用（单账户数秒到数十秒）
    # 期间请求主 session 的事务一直 "idle in transaction" 占锁。独立 session 在 finally
    # 关闭，事务立即释放。
    from backend.database.connection import SessionLocal
    hl_db = SessionLocal()
    try:
        snapshot = _get_hyperliquid_positions(hl_db, account_id, environment)
    finally:
        hl_db.close()

    accounts = (snapshot or {}).get("accounts") or []
    entry = next((a for a in accounts if a.get("account_id") == account_id), None)

    if not entry:
        base["error"] = "no_hyperliquid_wallet_for_environment"
        return

    base["account_name"] = entry.get("account_name")
    base["equity"] = entry.get("total_assets", 0.0)
    base["available_cash"] = entry.get("available_cash", 0.0)
    base["unrealized_pnl"] = entry.get("total_unrealized_pnl", 0.0)
    base["positions"] = [
        {
            "symbol": p.get("symbol"),
            "side": p.get("side"),
            "size": p.get("quantity"),
            "entry_price": p.get("avg_cost"),
            "mark_price": p.get("current_price"),
            "unrealized_pnl": p.get("unrealized_pnl"),
            "leverage": p.get("leverage"),
        }
        for p in entry.get("positions", [])
    ]
    base["active_positions"] = len(base["positions"])

    stats = _get_cached_trading_stats(db, account_id, environment)
    win_rate = stats.get("win_rate")
    base["win_rate"] = round(float(win_rate) * 100, 2) if win_rate is not None else 0.0
    base["total_trades"] = stats.get("total_trades", 0)
    base["realized_pnl"] = stats.get("total_pnl", 0.0)
    base["total_pnl"] = round(base["unrealized_pnl"] + base["realized_pnl"], 2)


def get_account_overview(
    db: Session,
    account_id: int,
    exchange: str,
    trading_mode: str,
) -> Dict[str, Any]:
    """单个「账户 x 交易所 x 模式」组合的统一概览。

    命中 TTL 缓存时直接返回缓存副本，避免短时间内（HTTP 轮询 + WS 变化信号 +
    多个 widget 各自读取）对同一账户重复触发实时行情查询而互相排队卡顿。
    """
    cache_key = f"{account_id}:{exchange}:{trading_mode}"
    now = time.time()
    cached = _overview_cache.get(cache_key)
    if cached and (now - cached["ts"]) < _OVERVIEW_TTL_SECONDS:
        return dict(cached["data"])

    base = _empty_overview(account_id, exchange, trading_mode)
    try:
        if trading_mode == "paper":
            _fill_paper_overview(db, account_id, base)
        elif trading_mode in ("testnet", "mainnet"):
            _fill_hyperliquid_overview(db, account_id, trading_mode, base)
        else:
            base["error"] = f"unsupported trading_mode: {trading_mode}"
    except Exception as exc:
        logger.error(
            f"[DashboardAggregator] get_account_overview failed "
            f"account={account_id} exchange={exchange} mode={trading_mode}: {exc}",
            exc_info=True,
        )
        base["error"] = str(exc)

    _overview_cache[cache_key] = {"ts": now, "data": base}
    return base


def get_accounts_overview(
    db: Session,
    selections: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """批量聚合多个「账户 x 交易所 x 模式」组合（前端多选对比模式）。

    selections: [{ "account_id": int, "exchange": str, "trading_mode": str }, ...]
    单个组合失败不影响其他组合返回（每条结果自带 error 字段）。

    [2026-07-09 性能修复] 原实现串行遍历每个账户，每个账户的交易所 REST 调用
    （10-12s）累加导致 5 账户 ≈ 50-60s。改为线程池并发，每个账户用独立的 DB
    session（不能并发共用同一连接），效果从串行累加 → 取最慢的一个（≈12s）。
    """
    # 归一化并过滤无效选项
    tasks: List[Dict[str, Any]] = []
    for sel in selections:
        account_id = sel.get("account_id")
        if account_id is None:
            continue
        tasks.append({
            "account_id": int(account_id),
            "exchange": str(sel.get("exchange") or "asterdex"),
            "trading_mode": str(sel.get("trading_mode") or "paper"),
        })

    if not tasks:
        return []

    # 单账户直接复用调用方传入的 session（无并发，避免无谓的 session 开销）
    if len(tasks) == 1:
        t = tasks[0]
        return [get_account_overview(db, t["account_id"], t["exchange"], t["trading_mode"])]

    # 多账户并发：每个任务独立 session，避免并发使用同一 DB 连接
    from backend.database.connection import SessionLocal

    def _worker(task: Dict[str, Any]) -> Dict[str, Any]:
        task_db = SessionLocal()
        try:
            return get_account_overview(
                task_db, task["account_id"], task["exchange"], task["trading_mode"]
            )
        finally:
            task_db.close()

    results: List[Dict[str, Any]] = []
    max_workers = min(8, len(tasks))
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="dashboard-overview") as pool:
        # 按 task 顺序提交，用 (index, future) 保持返回顺序与前端选择顺序一致
        future_to_index = {
            pool.submit(_worker, task): idx for idx, task in enumerate(tasks)
        }
        ordered: List[Optional[Dict[str, Any]]] = [None] * len(tasks)
        for future in as_completed(future_to_index):
            idx = future_to_index[future]
            try:
                ordered[idx] = future.result()
            except Exception as exc:
                logger.error(
                    f"[DashboardAggregator] get_accounts_overview task failed "
                    f"task={tasks[idx]}: {exc}",
                    exc_info=True,
                )
                ordered[idx] = _empty_overview(
                    tasks[idx]["account_id"],
                    tasks[idx]["exchange"],
                    tasks[idx]["trading_mode"],
                )
                ordered[idx]["error"] = str(exc)
        results = [r for r in ordered if r is not None]
    return results
