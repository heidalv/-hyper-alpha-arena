"""
固定时间窗策略运行复盘 — 6h / 24h / 7d 三域（AI / strategy / arb）

输出 JSON 到 data/strategy_runtime_reports/，供 PaperPaceController 与 OpenCode Context Pack 消费。
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

REPORT_DIR = os.path.join("data", "strategy_runtime_reports")
WINDOW_HOURS = {"6h": 6, "24h": 24, "7d": 168}


@dataclass
class RuntimeInsight:
    severity: str  # info | minor | major | critical
    category: str
    message: str
    metric: Optional[str] = None
    value: Optional[float] = None
    threshold: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity,
            "category": self.category,
            "message": self.message,
            "metric": self.metric,
            "value": self.value,
            "threshold": self.threshold,
        }


@dataclass
class StrategyRuntimeReport:
    window: str
    domain: str
    generated_at: str
    total_closed: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    max_single_loss: float = 0.0
    master_close_loss_ratio: float = 0.0
    master_close_count: int = 0
    close_reason_breakdown: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    by_tier: List[Dict[str, Any]] = field(default_factory=list)
    by_nature: List[Dict[str, Any]] = field(default_factory=list)
    by_symbol: List[Dict[str, Any]] = field(default_factory=list)
    insights: List[Dict[str, Any]] = field(default_factory=list)
    rule_breaches: List[str] = field(default_factory=list)
    arb: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "window": self.window,
            "domain": self.domain,
            "generated_at": self.generated_at,
            "total_closed": self.total_closed,
            "win_rate": round(self.win_rate, 4),
            "total_pnl": round(self.total_pnl, 2),
            "max_single_loss": round(self.max_single_loss, 2),
            "master_close_loss_ratio": round(self.master_close_loss_ratio, 4),
            "master_close_count": self.master_close_count,
            "close_reason_breakdown": self.close_reason_breakdown,
            "by_tier": self.by_tier,
            "by_nature": self.by_nature,
            "by_symbol": self.by_symbol,
            "insights": self.insights,
            "rule_breaches": self.rule_breaches,
            "arb": self.arb,
        }


def _window_hours(window: str) -> int:
    return WINDOW_HOURS.get(window, 24)


def _master_reason(reason: str) -> bool:
    r = (reason or "").lower()
    return r.startswith("master_running") or r.startswith("master_")


def _derive_rule_insights(report: StrategyRuntimeReport) -> List[RuntimeInsight]:
    insights: List[RuntimeInsight] = []
    if report.total_closed >= 10 and report.win_rate < 0.40:
        insights.append(RuntimeInsight(
            severity="major",
            category="win_rate",
            message=f"rolling {report.window} 胜率 {report.win_rate:.0%} < 40%",
            metric="win_rate",
            value=report.win_rate,
            threshold=0.40,
        ))
    # PnL 导向：负期望值告警（胜率高也可能触发）
    if report.total_closed >= 10:
        avg_pnl = report.total_pnl / report.total_closed
        if avg_pnl < -10:
            insights.append(RuntimeInsight(
                severity="critical",
                category="negative_ev",
                message=f"每笔期望收益 ${avg_pnl:+.1f} < -$10（严重亏损策略）",
                metric="avg_pnl_per_trade",
                value=avg_pnl,
                threshold=-10,
            ))
        elif avg_pnl < 0 and report.win_rate >= 0.45:
            insights.append(RuntimeInsight(
                severity="major",
                category="negative_ev_high_wr",
                message=f"胜率{report.win_rate:.0%}但每笔期望收益 ${avg_pnl:+.1f}（大亏小赚，盈亏比差）",
                metric="avg_pnl_per_trade",
                value=avg_pnl,
                threshold=0,
            ))
    if report.master_close_loss_ratio > 0.60 and report.master_close_count >= 3:
        insights.append(RuntimeInsight(
            severity="major",
            category="master_close",
            message=f"总控 close 亏损占比 {report.master_close_loss_ratio:.0%} > 60%",
            metric="master_close_loss_ratio",
            value=report.master_close_loss_ratio,
            threshold=0.60,
        ))
    elif report.master_close_loss_ratio > 0.40:
        insights.append(RuntimeInsight(
            severity="minor",
            category="master_close",
            message=f"总控 close 亏损占比 {report.master_close_loss_ratio:.0%} 偏高",
            metric="master_close_loss_ratio",
            value=report.master_close_loss_ratio,
            threshold=0.40,
        ))
    if report.max_single_loss < -300:
        insights.append(RuntimeInsight(
            severity="major",
            category="single_loss",
            message=f"单笔最大亏损 {report.max_single_loss:.0f} USDT",
            metric="max_single_loss",
            value=report.max_single_loss,
            threshold=-300.0,
        ))
    return insights


def build_ai_report(db, window: str = "24h", account_id: Optional[int] = None) -> StrategyRuntimeReport:
    from backend.services.trade_performance_analyzer import analyze_closed_trades

    hours = _window_hours(window)
    perf = analyze_closed_trades(db=db, since_hours=hours, account_id=account_id, exclude_rebate=True)
    report = StrategyRuntimeReport(
        window=window,
        domain="ai",
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_closed=perf.total_closed,
        win_rate=perf.overall_win_rate,
        total_pnl=perf.overall_pnl,
    )

    master_loss_pnl = 0.0
    total_loss_pnl = 0.0
    max_loss = 0.0
    breakdown: Dict[str, Dict[str, Any]] = {}
    for dim in perf.by_close_reason:
        breakdown[dim.key] = dim.to_dict()
        if dim.total_pnl < 0:
            total_loss_pnl += abs(dim.total_pnl)
            if _master_reason(dim.key):
                master_loss_pnl += abs(dim.total_pnl)
                report.master_close_count += dim.count

    loss_pnls = [d.avg_pnl for d in perf.by_symbol if d.avg_pnl < 0]
    report.max_single_loss = min(loss_pnls, default=0.0)
    report.close_reason_breakdown = breakdown
    report.by_tier = [d.to_dict() for d in perf.by_tier]
    report.by_nature = [d.to_dict() for d in perf.by_nature]
    report.by_symbol = [d.to_dict() for d in perf.by_symbol[:20]]

    if total_loss_pnl > 0:
        report.master_close_loss_ratio = master_loss_pnl / total_loss_pnl

    rule_insights = _derive_rule_insights(report)
    report.insights = [i.to_dict() for i in rule_insights]
    report.rule_breaches = [i.message for i in rule_insights if i.severity in ("major", "critical")]
    report.insights.extend([{"severity": "info", "category": "perf", "message": m} for m in perf.insights[:5]])
    return report


def build_strategy_report(db, window: str = "24h") -> StrategyRuntimeReport:
    from backend.database.models import StrategyMemory, StrategyTrade

    hours = _window_hours(window)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)
    trades = (
        db.query(StrategyTrade)
        .filter(
            StrategyTrade.status == "closed",
            StrategyTrade.closed_at >= cutoff,
            ~StrategyTrade.strategy_id.like("rebate_%"),
        )
        .all()
    )
    by_strategy: Dict[str, List[float]] = defaultdict(list)
    for t in trades:
        by_strategy[t.strategy_id or "unknown"].append(float(t.pnl or 0))

    report = StrategyRuntimeReport(
        window=window,
        domain="strategy",
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_closed=len(trades),
    )
    if trades:
        wins = sum(1 for t in trades if float(t.pnl or 0) > 0)
        report.win_rate = wins / len(trades)
        report.total_pnl = sum(float(t.pnl or 0) for t in trades)

    top_strategies = []
    for sid, pnls in sorted(by_strategy.items(), key=lambda x: -len(x[1]))[:15]:
        mem = db.query(StrategyMemory).filter(StrategyMemory.strategy_id == sid).first()
        top_strategies.append({
            "strategy_id": sid,
            "trades": len(pnls),
            "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else 0,
            "total_pnl": round(sum(pnls), 2),
            "memory_win_rate": float(mem.win_rate or 0) if mem else None,
            "key_lessons_count": len(mem.key_lessons or []) if mem else 0,
        })
    report.by_symbol = top_strategies  # reuse field for strategy list
    return report


def build_arb_report(db, window: str = "24h") -> StrategyRuntimeReport:
    """套利域 SRR — 同时纳入两轨已平仓数据，供 OpenCode 只读观测：
      - rebate 轨：RebateTradeOutcomeDB（S3/S8 返利/积分）
      - v3 轨：ArbitragePosition（资金费/跨所/基差，status='closed'）
    本报告纯观测，不触发任何自动改套利参数；V3 仍需手动开启方会产生数据。
    """
    from backend.database.models import RebateTradeOutcomeDB

    hours = _window_hours(window)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)

    # ── 轨 1：Rebate（返利/积分） ──
    rows = (
        db.query(RebateTradeOutcomeDB)
        .filter(
            RebateTradeOutcomeDB.mode == "paper",
            RebateTradeOutcomeDB.created_at >= cutoff,
        )
        .all()
    )

    # ── 轨 2：V3 统计套利（资金费/跨所/基差） ──
    v3_rows = []
    try:
        from backend.database.models import ArbitragePosition
        v3_rows = (
            db.query(ArbitragePosition)
            .filter(
                ArbitragePosition.mode == "paper",
                ArbitragePosition.status == "closed",
                ArbitragePosition.close_time >= cutoff,
            )
            .all()
        )
    except Exception as err:  # 表缺失/迁移未跑时静默降级，不阻断 rebate 统计
        logger.debug("[SRR] V3 套利数据加载跳过: %s", err)

    report = StrategyRuntimeReport(
        window=window,
        domain="arb",
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_closed=len(rows) + len(v3_rows),
    )

    arb_block: Dict[str, Any] = {}

    # rebate 统计
    if rows:
        rb_wins = sum(1 for r in rows if float(r.net_value or 0) > 0)
        rb_pnl = sum(float(r.net_value or 0) for r in rows)
        points = sum(float(r.points or 0) for r in rows)
        cash_pt = rb_pnl / points if points else 0.0
        arb_block["rebate"] = {
            "samples": len(rows),
            "win_rate": round(rb_wins / len(rows), 4),
            "points": round(points, 2),
            "cash_pt": round(cash_pt, 4),
            "net_value": round(rb_pnl, 2),
        }
        if cash_pt < -0.08:
            report.rule_breaches.append(f"S8 cash/pt {cash_pt:.4f} < -0.08")
            report.insights.append({
                "severity": "major",
                "category": "arb_s8",
                "message": report.rule_breaches[-1],
            })

    # v3 统计（按 strategy 拆分）
    if v3_rows:
        def _pnl(p) -> float:
            return float(p.pnl or 0)
        v3_wins = sum(1 for p in v3_rows if _pnl(p) > 0)
        v3_pnl = sum(_pnl(p) for p in v3_rows)
        by_strategy: Dict[str, Dict[str, Any]] = {}
        for p in v3_rows:
            strat = str(getattr(p, "strategy", None) or "unknown")
            slot = by_strategy.setdefault(strat, {"samples": 0, "wins": 0, "pnl": 0.0})
            slot["samples"] += 1
            slot["wins"] += 1 if _pnl(p) > 0 else 0
            slot["pnl"] += _pnl(p)
        for slot in by_strategy.values():
            slot["win_rate"] = round(slot["wins"] / slot["samples"], 4) if slot["samples"] else 0.0
            slot["pnl"] = round(slot["pnl"], 2)
            slot.pop("wins", None)
        arb_block["v3"] = {
            "samples": len(v3_rows),
            "win_rate": round(v3_wins / len(v3_rows), 4),
            "net_value": round(v3_pnl, 2),
            "by_strategy": by_strategy,
        }

    # 合并总览（两轨净值/胜率汇总，供顶层 win_rate/total_pnl 展示）
    total_pnl = 0.0
    total_wins = 0
    if rows:
        total_pnl += arb_block["rebate"]["net_value"]
        total_wins += int(round(arb_block["rebate"]["win_rate"] * len(rows)))
    if v3_rows:
        total_pnl += arb_block["v3"]["net_value"]
        total_wins += int(round(arb_block["v3"]["win_rate"] * len(v3_rows)))
    if report.total_closed:
        report.win_rate = total_wins / report.total_closed
    report.total_pnl = round(total_pnl, 2)
    report.arb = arb_block or None
    return report


def build_ai_report_since(
    db,
    since_at: datetime,
    *,
    account_id: Optional[int] = None,
) -> StrategyRuntimeReport:
    """仅统计 since_at 之后平仓的 AI 域成交（提案 post-apply 验证用）。"""
    from backend.services.trade_performance_analyzer import analyze_closed_trades

    perf = analyze_closed_trades(db=db, since_at=since_at, account_id=account_id, exclude_rebate=True)
    label = since_at.strftime("%Y%m%d_%H%M") if since_at else "since"
    report = StrategyRuntimeReport(
        window=f"since_{label}",
        domain="ai",
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_closed=perf.total_closed,
        win_rate=perf.overall_win_rate,
        total_pnl=perf.overall_pnl,
    )

    master_loss_pnl = 0.0
    total_loss_pnl = 0.0
    breakdown: Dict[str, Dict[str, Any]] = {}
    for dim in perf.by_close_reason:
        breakdown[dim.key] = dim.to_dict()
        if dim.total_pnl < 0:
            total_loss_pnl += abs(dim.total_pnl)
            if _master_reason(dim.key):
                master_loss_pnl += abs(dim.total_pnl)
                report.master_close_count += dim.count

    loss_pnls = [d.avg_pnl for d in perf.by_symbol if d.avg_pnl < 0]
    report.max_single_loss = min(loss_pnls, default=0.0)
    report.close_reason_breakdown = breakdown
    report.by_tier = [d.to_dict() for d in perf.by_tier]
    report.by_nature = [d.to_dict() for d in perf.by_nature]
    report.by_symbol = [d.to_dict() for d in perf.by_symbol[:20]]
    if total_loss_pnl > 0:
        report.master_close_loss_ratio = master_loss_pnl / total_loss_pnl

    rule_insights = _derive_rule_insights(report)
    report.insights = [i.to_dict() for i in rule_insights]
    report.rule_breaches = [i.message for i in rule_insights if i.severity in ("major", "critical")]
    report.insights.extend([{"severity": "info", "category": "perf", "message": m} for m in perf.insights[:5]])
    return report


def generate_report(db, window: str = "24h", domain: str = "ai", account_id: Optional[int] = None) -> StrategyRuntimeReport:
    if domain == "strategy":
        return build_strategy_report(db, window)
    if domain == "arb":
        return build_arb_report(db, window)
    return build_ai_report(db, window, account_id=account_id)


def save_report(report: StrategyRuntimeReport) -> str:
    os.makedirs(REPORT_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    fname = f"{report.window}_{report.domain}_{ts}.json"
    path = os.path.join(REPORT_DIR, fname)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
    latest = os.path.join(REPORT_DIR, f"latest_{report.window}_{report.domain}.json")
    if report.total_closed <= 0 and os.path.isfile(latest):
        try:
            with open(latest, encoding="utf-8") as f:
                old = json.load(f)
            if int(old.get("total_closed") or 0) > 0:
                logger.warning(
                    "[SRR] 拒绝用空报告覆盖 latest_%s_%s（已有 %s 笔）",
                    report.window,
                    report.domain,
                    old.get("total_closed"),
                )
                return path
        except Exception:
            pass
    with open(latest, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
    return path


def load_latest_report(window: str = "24h", domain: str = "ai") -> Optional[Dict[str, Any]]:
    path = os.path.join(REPORT_DIR, f"latest_{window}_{domain}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as err:
        logger.warning("[SRR] 读取 latest 失败: %s", err)
        return None


def _report_is_empty(data: Optional[Dict[str, Any]]) -> bool:
    if not data:
        return True
    return int(data.get("total_closed") or 0) <= 0


def _db_has_closed_trades(db, *, hours: int) -> bool:
    from backend.database.models import PaperPosition

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)
    row = (
        db.query(PaperPosition)
        .filter(
            PaperPosition.status == "closed",
            PaperPosition.closed_at >= cutoff,
        )
        .limit(1)
        .first()
    )
    return row is not None


def get_or_build_runtime_report(
    db,
    window: str = "24h",
    domain: str = "ai",
    *,
    force_refresh: bool = False,
    account_id: Optional[int] = None,
) -> Dict[str, Any]:
    """从 DB 生成 SRR；拒绝用过期的空 latest 缓存糊弄 OpenCode。"""
    cached = None if force_refresh else load_latest_report(window, domain)
    hours = _window_hours(window)

    if cached and not _report_is_empty(cached):
        return cached

    if cached and _report_is_empty(cached) and not _db_has_closed_trades(db, hours=hours):
        return cached

    if cached and _report_is_empty(cached):
        logger.warning(
            "[SRR] latest_%s_%s 为空但 DB 有成交，强制从 DB 重建",
            window,
            domain,
        )

    report = generate_report(db, window=window, domain=domain, account_id=account_id)
    save_report(report)
    return report.to_dict()


def run_report_tick(windows: Optional[List[str]] = None, domains: Optional[List[str]] = None) -> List[str]:
    from backend.database.connection import SessionLocal, DATABASE_URL

    windows = windows or ["6h", "24h"]
    domains = domains or ["ai", "arb"]
    paths: List[str] = []
    db = SessionLocal()
    try:
        for window in windows:
            for domain in domains:
                try:
                    data = get_or_build_runtime_report(
                        db, window=window, domain=domain, force_refresh=True,
                    )
                    latest = os.path.join(REPORT_DIR, f"latest_{window}_{domain}.json")
                    paths.append(latest)
                    if _report_is_empty(data):
                        logger.warning(
                            "[SRR] %s/%s 报告为空 (DB=%s)，请确认 DATABASE_URL 指向含成交的库",
                            window,
                            domain,
                            DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL[:40],
                        )
                except Exception as err:
                    logger.error("[SRR] %s/%s 失败: %s", window, domain, err, exc_info=True)
        if "24h" in windows:
            try:
                data = get_or_build_runtime_report(db, window="7d", domain="ai", force_refresh=True)
                paths.append(os.path.join(REPORT_DIR, "latest_7d_ai.json"))
                if _report_is_empty(data):
                    logger.warning("[SRR] 7d/ai 报告为空")
            except Exception as err:
                logger.error("[SRR] 7d/ai 失败: %s", err)
    finally:
        db.close()
    return paths
