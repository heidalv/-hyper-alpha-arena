"""Stage F 滚动监控 + 自动熔断回滚 — 激进路径的底线保护。

对齐 docs/research/cross_review.md §R5-2 (KPI 表) 与 Stage G 最终裁决。

用途:
    定时（建议 crontab 每 15 分钟）跑一次本脚本。
    它会从 alpha_arena.db 实时计算 7 条 KPI，与 Stage F 阈值对比，任何一条
    触发熔断都会把一个 flag 文件写入，让交易服务下次启动/reload 时自动把
    LEGACY_RISK_HARD_ROLLBACK 切到 true，等效把 Stage E 整体关掉，回到旧路径。

本脚本绝不主动改 settings.py 代码；只写 flag 文件 data/stage_f_rollback.flag。
在 `backend/config/settings.py` 里 LEGACY_RISK_HARD_ROLLBACK 的默认值 reader
应额外读这个 flag 文件（Stage E 上线后补 1 行，详见本文件末尾 README）。

用法:
    python scripts/stage_f_monitor.py --db data/alpha_arena.db \
        --since-hours 24 --equity 10000 \
        --out docs/research/stage_f_report.md
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("stage_f_monitor")

# ───────── KPI 阈值表（等价 cross_review.md §R5-2） ─────────
# key: KPI 名  value: {"target_low": x, "target_high": y, "hard_trip_if_below": a, "hard_trip_if_above": b}
#   None 表示该方向不熔断
KPI_THRESHOLDS = {
    "sl_trigger_rate_7d":      {"target_low": 0.04, "target_high": 0.10, "hard_above": 0.20, "hard_below": None},
    "avg_leverage_7d":         {"target_low": 0.0,  "target_high": 14,   "hard_above": 17,   "hard_below": None},
    "bucket_concurrency_peak": {"target_low": 0,    "target_high": 3,    "hard_above": 4,    "hard_below": None},
    "missing_nature_ratio_24h":{"target_low": 0.0,  "target_high": 0.10, "hard_above": 0.50, "hard_below": None},
    "xpl_trade_count_7d":      {"target_low": 10,   "target_high": 30,   "hard_above": None, "hard_below": 3},
    "cum_pnl_pct_7d":          {"target_low": -0.03,"target_high": None, "hard_above": None, "hard_below": -0.05},
    "heartbeat_hours":         {"target_low": 0,    "target_high": 1,    "hard_above": 6,    "hard_below": None},
}

ROLLBACK_FLAG_PATH = Path("data/stage_f_rollback.flag")


# ═══════════════════════════════════════════════════════════════════════
# KPI 计算
# ═══════════════════════════════════════════════════════════════════════
def _conn(db: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    return c


def kpi_sl_trigger_rate(conn: sqlite3.Connection, since_iso: str) -> float:
    total = conn.execute(
        "SELECT COUNT(*) FROM paper_orders WHERE status='filled' AND close_reason IS NOT NULL AND filled_at >= ?",
        (since_iso,),
    ).fetchone()[0]
    if not total:
        return 0.0
    sl_hit = conn.execute(
        "SELECT COUNT(*) FROM paper_orders WHERE status='filled' AND close_reason='sl' AND filled_at >= ?",
        (since_iso,),
    ).fetchone()[0]
    return sl_hit / total


def kpi_avg_leverage(conn: sqlite3.Connection, since_iso: str) -> float:
    rows = conn.execute(
        "SELECT leverage FROM paper_orders WHERE status='filled' AND leverage IS NOT NULL AND filled_at >= ?",
        (since_iso,),
    ).fetchall()
    vals = [float(r[0]) for r in rows if r[0] is not None]
    return mean(vals) if vals else 0.0


def kpi_bucket_concurrency_peak(conn: sqlite3.Connection, since_iso: str) -> int:
    """峰值同 bucket 并发持仓数（用开仓 - 平仓近似，计算简化版）."""
    try:
        from backend.services.risk_band_resolver import get_correlation_bucket
    except Exception:
        return 0
    orders = conn.execute(
        "SELECT symbol, close_reason, filled_at FROM paper_orders "
        "WHERE status='filled' AND filled_at >= ? ORDER BY filled_at ASC",
        (since_iso,),
    ).fetchall()
    bucket_count: dict[str, int] = {}
    peak = 0
    for o in orders:
        sym = (o["symbol"] or "").upper()
        b = get_correlation_bucket(sym)
        if not b:
            continue
        name = b["name"]
        if o["close_reason"]:
            bucket_count[name] = max(0, bucket_count.get(name, 0) - 1)
        else:
            bucket_count[name] = bucket_count.get(name, 0) + 1
            peak = max(peak, bucket_count[name])
    return peak


def kpi_missing_nature_ratio(conn: sqlite3.Connection, since_iso: str) -> float:
    total = conn.execute(
        "SELECT COUNT(*) FROM paper_orders WHERE status='filled' AND close_reason IS NULL AND filled_at >= ?",
        (since_iso,),
    ).fetchone()[0]
    if not total:
        return 0.0
    missing = conn.execute(
        "SELECT COUNT(*) FROM paper_orders WHERE status='filled' AND close_reason IS NULL "
        "AND (trade_nature IS NULL OR trade_nature='') AND filled_at >= ?",
        (since_iso,),
    ).fetchone()[0]
    return missing / total


def kpi_xpl_trade_count(conn: sqlite3.Connection, since_iso: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM paper_orders WHERE status='filled' AND symbol='XPL' AND filled_at >= ?",
        (since_iso,),
    ).fetchone()[0]


def kpi_cum_pnl_pct(conn: sqlite3.Connection, since_iso: str, equity: float) -> float:
    row = conn.execute(
        "SELECT SUM(pnl) FROM paper_orders WHERE status='filled' AND filled_at >= ? AND pnl IS NOT NULL",
        (since_iso,),
    ).fetchone()
    pnl = float(row[0] or 0)
    return pnl / equity if equity > 0 else 0.0


def kpi_heartbeat_hours(conn: sqlite3.Connection) -> float:
    """距最近一次成交多久（若 heartbeat > 6h 说明系统挂了，需要熔断）"""
    row = conn.execute(
        "SELECT MAX(filled_at) FROM paper_orders WHERE status='filled'"
    ).fetchone()
    if not row or not row[0]:
        return 999.0
    try:
        last = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
    except Exception:
        try:
            last = datetime.fromisoformat(row[0])
        except Exception:
            return 999.0
    now = datetime.now(last.tzinfo) if last.tzinfo else datetime.now()
    return (now - last).total_seconds() / 3600


# ═══════════════════════════════════════════════════════════════════════
# 报告 + 熔断决策
# ═══════════════════════════════════════════════════════════════════════
def evaluate_kpi(name: str, value: float) -> tuple[str, str]:
    """返回 (状态, 说明). 状态 ∈ {'ok', 'warn', 'trip'}."""
    cfg = KPI_THRESHOLDS[name]
    above = cfg.get("hard_above")
    below = cfg.get("hard_below")
    if above is not None and value > above:
        return ("trip", f"{value} > {above} (hard_above)")
    if below is not None and value < below:
        return ("trip", f"{value} < {below} (hard_below)")
    tgt_low = cfg.get("target_low")
    tgt_high = cfg.get("target_high")
    if tgt_high is not None and value > tgt_high:
        return ("warn", f"{value} > target_high {tgt_high}")
    if tgt_low is not None and value < tgt_low:
        return ("warn", f"{value} < target_low {tgt_low}")
    return ("ok", f"{value}")


def build_report(values: dict, statuses: dict, dry_run: bool = False) -> str:
    lines = [
        "# Stage F 滚动监控报告",
        f"\n生成时间: {datetime.now(timezone.utc).isoformat()}",
        "\n## KPI 当前值",
        "",
        "| KPI | 值 | 状态 | 说明 |",
        "|---|---|---|---|",
    ]
    for k, v in values.items():
        status, info = statuses[k]
        emoji = {"ok": "OK", "warn": "WARN", "trip": "TRIP"}[status]
        lines.append(f"| {k} | {v} | **{emoji}** | {info} |")
    lines.append("\n## 熔断判定")
    trips = [k for k, (s, _) in statuses.items() if s == "trip"]
    if trips:
        lines.append("**已触发熔断**：" + ", ".join(trips))
        if dry_run:
            lines.append(f"\n[DRY-RUN] 未写入 flag 文件。实际运行时会落 `{ROLLBACK_FLAG_PATH}`。")
        else:
            lines.append(f"\n回滚 flag 文件: `{ROLLBACK_FLAG_PATH}` 已写入；重启交易服务即自动回旧路径。")
    else:
        lines.append("未触发熔断，所有 KPI 在硬阈值内。")
    return "\n".join(lines)


def write_rollback_flag(reasons: list[str]) -> None:
    ROLLBACK_FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "triggered_by": reasons,
    }
    ROLLBACK_FLAG_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.warning(f"[Stage F] 熔断 flag 已写入: {ROLLBACK_FLAG_PATH}  (reasons={reasons})")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/alpha_arena.db")
    p.add_argument("--since-hours", type=int, default=24)
    p.add_argument("--equity", type=float, default=10000.0, help="账户本金估值，用于 cum_pnl_pct")
    p.add_argument("--out", default="docs/research/stage_f_report.md")
    p.add_argument("--dry-run", action="store_true", help="仅出报告，不写 rollback flag")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    db = Path(args.db)
    if not db.exists():
        logger.error(f"DB 不存在: {db}")
        return 1

    since = (datetime.now(timezone.utc) - timedelta(hours=args.since_hours)).isoformat(" ")

    with _conn(db) as conn:
        values: dict[str, Any] = {
            "sl_trigger_rate_7d":      round(kpi_sl_trigger_rate(conn, since), 4),
            "avg_leverage_7d":         round(kpi_avg_leverage(conn, since), 2),
            "bucket_concurrency_peak": kpi_bucket_concurrency_peak(conn, since),
            "missing_nature_ratio_24h":round(kpi_missing_nature_ratio(conn, since), 4),
            "xpl_trade_count_7d":      kpi_xpl_trade_count(conn, since),
            "cum_pnl_pct_7d":          round(kpi_cum_pnl_pct(conn, since, args.equity), 4),
            "heartbeat_hours":         round(kpi_heartbeat_hours(conn), 2),
        }

    statuses = {k: evaluate_kpi(k, v) for k, v in values.items()}
    trips = [k for k, (s, _) in statuses.items() if s == "trip"]

    report = build_report(values, statuses, dry_run=args.dry_run)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    logger.info(f"报告已写入 {out}")

    if trips and not args.dry_run:
        write_rollback_flag(trips)
        return 2  # 非零退出，便于 cron 告警
    return 0


if __name__ == "__main__":
    sys.exit(main())
