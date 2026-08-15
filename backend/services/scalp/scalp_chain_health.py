"""短线链路健康巡检（只读，10 分钟级）。

检查项：
- 5m K 线新鲜度（全局 + 近 7 日活跃币）
- 信号结算积压（settled=false 且超过结算周期）
- 结算延迟（近 6h 已结算信号的平均/最大 signal_ts→settle_ts）
- 学习产物时效（meta 报告 / 运行时权重文件 mtime）

输出：reports/scalp_chain/scalp_chain_YYYY-MM-DD-HHMM.json
不改交易行为，失败只告警不中断。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from sqlalchemy import text

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[3]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _file_age_min(path: Path) -> int:
    try:
        if not path.is_file():
            return -1
        return int((_now_utc().timestamp() - path.stat().st_mtime) / 60.0)
    except Exception:
        return -1


def _active_symbols(days: int = 7) -> List[str]:
    from backend.database.connection import SessionLocal
    from backend.core.tenant import system_identity

    start_ts = int((_now_utc() - timedelta(days=days)).timestamp())
    with system_identity():
        with SessionLocal() as db:
            rows = db.execute(
                text(
                    "SELECT symbol, COUNT(*) AS n FROM scalp_signal_log "
                    "WHERE signal_ts >= :start GROUP BY symbol ORDER BY n DESC LIMIT 10"
                ),
                {"start": start_ts},
            ).mappings().all()
    return [str(r["symbol"]) for r in rows]


def run_scalp_chain_health() -> Dict[str, Any]:
    from backend.database.connection import SessionLocal, MarketSessionLocal
    from backend.core.tenant import system_identity

    now = _now_utc()
    now_ts = int(now.timestamp())
    report: Dict[str, Any] = {
        "generated_at": now.isoformat(),
        "kline_freshness": {},
        "settlement": {},
        "learning_artifacts": {},
        "issues": [],
    }

    symbols = _active_symbols()
    with system_identity():
        with MarketSessionLocal() as db:
            row = db.execute(
                text(
                    "SELECT MAX(timestamp) AS max_ts, COUNT(*) AS n "
                    "FROM crypto_klines WHERE period = '5m'"
                )
            ).mappings().first()
            if row and row["max_ts"]:
                max_ts = int(row["max_ts"])
                report["kline_freshness"]["global_max_ts"] = max_ts
                report["kline_freshness"]["freshness_sec"] = now_ts - max_ts
                report["kline_freshness"]["count"] = int(row["n"] or 0)
            by_symbol: Dict[str, Dict[str, Any]] = {}
            for sym in symbols:
                row = db.execute(
                    text(
                        "SELECT MAX(timestamp) AS max_ts, COUNT(*) AS n "
                        "FROM crypto_klines WHERE period = '5m' AND symbol = :sym"
                    ),
                    {"sym": sym},
                ).mappings().first()
                if row and row["max_ts"]:
                    by_symbol[sym] = {
                        "freshness_sec": now_ts - int(row["max_ts"]),
                        "count": int(row["n"] or 0),
                    }
            report["kline_freshness"]["by_symbol"] = by_symbol

        with SessionLocal() as db:
            horizon_sec = 1800
            row = db.execute(
                text(
                    "SELECT COUNT(*) AS n, MIN(signal_ts) AS oldest "
                    "FROM scalp_signal_log "
                    "WHERE settled = FALSE AND signal_ts < :cutoff"
                ),
                {"cutoff": now_ts - horizon_sec},
            ).mappings().first()
            report["settlement"]["unsettled_backlog"] = int(row["n"] or 0) if row else 0
            report["settlement"]["oldest_unsettled_ts"] = int(row["oldest"] or 0) if row else None

            row = db.execute(
                text(
                    "SELECT AVG(settle_ts - signal_ts) AS avg_lag, "
                    "MAX(settle_ts - signal_ts) AS max_lag, COUNT(*) AS n "
                    "FROM scalp_signal_log "
                    "WHERE settled = TRUE AND settle_ts >= :since"
                ),
                {"since": now_ts - 6 * 3600},
            ).mappings().first()
            if row and row["n"]:
                report["settlement"]["avg_lag_sec"] = round(float(row["avg_lag"]), 1)
                report["settlement"]["max_lag_sec"] = int(row["max_lag"] or 0)
                report["settlement"]["settled_last_6h"] = int(row["n"])

    meta_age = _file_age_min(_ROOT / "data" / "scalp_meta_report.json")
    weight_age = _file_age_min(_ROOT / "data" / "factor_runtime_weights.json")
    report["learning_artifacts"] = {
        "scalp_meta_report_age_min": meta_age,
        "factor_runtime_weights_age_min": weight_age,
    }

    fresh = report["kline_freshness"].get("freshness_sec")
    if fresh is not None and fresh > 600:
        report["issues"].append(
            {"severity": "critical", "item": "kline_freshness", "detail": "5m 最新 K 线超过 10 分钟"}
        )
    backlog = report["settlement"].get("unsettled_backlog", 0)
    if backlog > 5000:
        report["issues"].append(
            {"severity": "warning", "item": "settlement", "detail": "未结算积压 %s 条" % backlog}
        )
    if meta_age is not None and meta_age > 30 * 24 * 60:
        report["issues"].append(
            {"severity": "warning", "item": "learning_artifacts", "detail": "meta 报告超过 30 天未更新"}
        )
    if weight_age is not None and weight_age > 60:
        report["issues"].append(
            {"severity": "info", "item": "learning_artifacts", "detail": "运行时权重超过 60 分钟未更新"}
        )

    out_dir = _ROOT / "reports" / "scalp_chain"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / ("scalp_chain_%s.json" % now.strftime("%Y-%m-%d-%H%M"))
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[ScalpChain] 巡检完成: issues=%s, %s", len(report["issues"]), path)
    try:
        from backend.services.scalp.scalp_heartbeat import touch
        touch("scalp_chain_health", "ok", {
            "issues": len(report["issues"]),
            # [2026-08-15] 带问题明细，面板可直接定位（原来只有计数）
            "issue_list": report["issues"][:5],
            "circuit_breaker": report.get("circuit_breaker", {}),
        })
    except Exception:
        pass
    return report


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))

    rep = run_scalp_chain_health()
    print(json.dumps(rep, ensure_ascii=False, indent=2))
