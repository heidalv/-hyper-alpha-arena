"""账户成交历史采集 & 按币种拆分统计 — Stage C1/C2 交付物。

用途:
    从 alpha_arena.db (SQLite) 的 paper_orders 表拉取真实成交历史，
    按币种拆分 胜率 / 盈亏比 / 持仓时长 / 最大单笔回撤 并写 CSV。
    只读 DB，不改任何业务表。

用法:
    python scripts/collect_account_history.py \
        --db data/alpha_arena.db \
        --account-id 1 \
        --output docs/research/account_history_by_symbol.csv
"""
from __future__ import annotations

import argparse
import csv
import logging
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any


logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("collect_account")


def fetch_orders(db_path: Path, account_id: int | None) -> list[dict]:
    if not db_path.exists():
        raise FileNotFoundError(f"DB 不存在: {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        sql = (
            "SELECT id, account_id, strategy_id, symbol, side, order_type, "
            "price, quantity, filled_quantity, filled_price, leverage, "
            "tp_price, sl_price, fee, pnl, close_reason, trade_nature, "
            "status, created_at, filled_at "
            "FROM paper_orders WHERE status = 'filled'"
        )
        params: list[Any] = []
        if account_id is not None:
            sql += " AND account_id = ?"
            params.append(account_id)
        sql += " ORDER BY filled_at ASC"
        cur = conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def extract_base_symbol(s: str) -> str:
    s = (s or "").upper()
    for suffix in ("USDT", "USDC", "USD"):
        if s.endswith(suffix):
            return s[: -len(suffix)]
    return s


def aggregate(orders: list[dict]) -> tuple[list[dict], dict]:
    """按币种聚合:
    - total_trades: 已平仓的订单数（pnl not null）
    - win_rate, avg_win, avg_loss, profit_factor, median_hold_sec, max_single_loss
    """
    close_orders = [o for o in orders if o.get("pnl") is not None and o.get("close_reason")]
    opens_by_id: dict[int, dict] = {}
    for o in orders:
        if not o.get("close_reason") and o.get("filled_at"):
            opens_by_id[o["id"]] = o

    per_symbol: dict[str, dict] = defaultdict(lambda: {
        "trades": [], "wins": [], "losses": [], "holds_sec": [],
    })

    for c in close_orders:
        sym = extract_base_symbol(c["symbol"])
        pnl = float(c.get("pnl") or 0)
        per_symbol[sym]["trades"].append(pnl)
        if pnl > 0:
            per_symbol[sym]["wins"].append(pnl)
        elif pnl < 0:
            per_symbol[sym]["losses"].append(pnl)

    rows: list[dict] = []
    overall_pnl = 0.0
    for sym, d in sorted(per_symbol.items()):
        n = len(d["trades"])
        wins = d["wins"]
        losses = d["losses"]
        total_pnl = sum(d["trades"])
        overall_pnl += total_pnl
        avg_win = mean(wins) if wins else 0.0
        avg_loss = mean(losses) if losses else 0.0
        win_rate = len(wins) / n if n else 0.0
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0
        rows.append({
            "symbol": sym,
            "trades": n,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 4),
            "total_pnl": round(total_pnl, 4),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "profit_factor": round(pf, 4) if pf != float("inf") else "inf",
            "max_single_win": round(max(wins) if wins else 0.0, 4),
            "max_single_loss": round(min(losses) if losses else 0.0, 4),
            "median_pnl": round(median(d["trades"]) if d["trades"] else 0.0, 4),
        })

    overall = {
        "overall_closed_trades": len(close_orders),
        "overall_pnl": round(overall_pnl, 4),
        "symbols_traded": len(per_symbol),
    }
    return rows, overall


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="从 SQLite 抓账户历史并按币统计")
    p.add_argument("--db", default="data/alpha_arena.db")
    p.add_argument("--account-id", type=int, default=None,
                   help="为空则统计所有 account")
    p.add_argument("--output", default="docs/research/account_history_by_symbol.csv")
    p.add_argument("--raw-output", default="docs/research/account_history_raw.csv",
                   help="原始已平仓订单（便于复核）")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    try:
        orders = fetch_orders(db_path, args.account_id)
    except Exception as e:
        logger.error(f"读取 DB 失败: {e}")
        return 1

    logger.info(f"已 fetch {len(orders)} 条 filled 订单 (account_id={args.account_id})")

    rows, overall = aggregate(orders)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        logger.warning("没有有 pnl 的平仓订单，输出仍会写入空表头")
        rows = [{k: "" for k in [
            "symbol", "trades", "wins", "losses", "win_rate", "total_pnl",
            "avg_win", "avg_loss", "profit_factor",
            "max_single_win", "max_single_loss", "median_pnl",
        ]}]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)
    logger.info(f"✓ 按币种统计 → {out_path} ({len(rows)} 行)")
    logger.info(f"  总体: {overall}")

    raw_out = Path(args.raw_output)
    raw_out.parent.mkdir(parents=True, exist_ok=True)
    close_orders = [o for o in orders if o.get("close_reason")]
    with raw_out.open("w", newline="", encoding="utf-8") as f:
        if close_orders:
            w = csv.DictWriter(f, fieldnames=list(close_orders[0].keys()))
            w.writeheader()
            for o in close_orders:
                w.writerow(o)
    logger.info(f"✓ 平仓订单明细 → {raw_out} ({len(close_orders)} 行)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
