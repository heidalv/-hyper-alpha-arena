# -*- coding: utf-8 -*-
"""
ai_governed_compare — v6 阶段 2（S2-6）灰度阶梯决策日志对比分析器

计划 6.3 第 7 项：ai_governed 灰度 0.40 → 0.60 → 1.0，每步 G1/paper 48h +
决策日志对比，任一环节劣化 → 一键回滚（MLTO_AI_GOVERNED=0）。

本模块提供：
1. `snapshot_with_hub_mode`：把当前 hub 模式（standard / ai_governed）与
   灰度权重档位注入 decision_snapshot（落库点在 master_execution 等）。
2. `collect_gray_metrics`：按模式分组聚合 ai_decision_logs 近 N 天指标
   （决策量/开仓量/执行率/平均 confidence/命中率/盈亏比）。
3. `judge_deterioration`：灰度劣化判定（governed 命中率显著低于 standard
   → 建议回滚；样本不足 → 未定论）。

判定口径：
- 命中 = realized_pnl > 0；亏损 = realized_pnl < 0（realized_pnl 非空记录）
- 盈亏比 = 平均盈利 / |平均亏损|（无亏损记录时为 None）
- 劣化阈值：governed 命中率 < standard 命中率 − DETERIORATION_WIN_RATE_GAP（5pp）
  或 governed 盈亏比 < standard 盈亏比 × (1 − DETERIORATION_PF_DROP)（20%）
- 最小样本：MODE_MIN_REALIZED_SAMPLES=20（不足 → status=insufficient）
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

MODE_GOVERNED = "ai_governed"
MODE_STANDARD = "standard"

_LOOKBACK_DAYS = int(os.getenv("AI_GOVERNED_COMPARE_DAYS", "7"))
_MIN_REALIZED_SAMPLES = int(os.getenv("AI_GOVERNED_COMPARE_MIN_SAMPLES", "20"))
_DETERIORATION_WIN_RATE_GAP = float(os.getenv("AI_GOVERNED_WIN_RATE_GAP", "0.05"))
_DETERIORATION_PF_DROP = float(os.getenv("AI_GOVERNED_PF_DROP", "0.20"))


def snapshot_with_hub_mode(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """把当前 hub 模式与灰度权重档位注入决策快照（JSON 安全，失败不改动）。"""
    try:
        from backend.services.mlto import decision_hub as _dh
        out = dict(snapshot or {})
        if _dh.ai_governed_enabled():
            out["hub_mode"] = MODE_GOVERNED
            out["ai_governed_weight"] = _dh.ai_governed_weight()
        else:
            out["hub_mode"] = MODE_STANDARD
            out["ai_governed_weight"] = None
        return out
    except Exception:
        return dict(snapshot or {})


def extract_hub_mode(snapshot: Any) -> str:
    """从 decision_snapshot（str/None/dict）提取 hub 模式，默认 standard。"""
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except Exception:
            return MODE_STANDARD
    if isinstance(snapshot, dict):
        m = str(snapshot.get("hub_mode") or MODE_STANDARD).strip().lower()
        if m in (MODE_GOVERNED, MODE_STANDARD):
            return m
    return MODE_STANDARD


def _row_confidence(row: Dict[str, Any]) -> Optional[float]:
    snap = row.get("decision_snapshot")
    if isinstance(snap, str):
        try:
            snap = json.loads(snap)
        except Exception:
            snap = None
    if isinstance(snap, dict):
        try:
            c = float(snap.get("confidence") or 0)
            if 0 <= c <= 1:
                return c
        except (TypeError, ValueError):
            pass
    return None


def collect_gray_metrics(
    db=None,
    *,
    days: Optional[int] = None,
    account_id: Optional[int] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    按模式分组聚合近 days 天 ai_decision_logs 灰度指标。

    返回 {mode: metrics}，metrics 字段：
      decisions / opens / executed / hold_pct / mean_confidence /
      realized_count / wins / losses / win_rate / avg_pnl / profit_factor
    db 为 None 时用 AnalyticsSessionLocal（生产路径）。
    使用 ORM + Python 侧时间过滤，规避 PG/SQLite 方言差异。
    """
    from datetime import datetime, timedelta, timezone
    from backend.database.models import AIDecisionLog

    if db is None:
        from backend.database.connection import AnalyticsSessionLocal
        db = AnalyticsSessionLocal()
    lookback = int(days or _LOOKBACK_DAYS)
    start_dt = datetime.now(timezone.utc) - timedelta(days=lookback)

    q = db.query(AIDecisionLog)
    if account_id is not None:
        q = q.filter(AIDecisionLog.account_id == account_id)
    rows = q.all()

    groups: Dict[str, Dict[str, Any]] = {
        MODE_STANDARD: _empty_metrics(),
        MODE_GOVERNED: _empty_metrics(),
    }
    for r in rows:
        # Python 侧时间过滤（兼容 TIMESTAMP 无时区/字符串存储）
        dt = r.decision_time
        if dt is not None:
            try:
                if isinstance(dt, str):
                    dt = datetime.fromisoformat(str(dt).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt < start_dt:
                    continue
            except (TypeError, ValueError):
                pass  # 无法解析的时间不拦截（保守保留）
        mode = extract_hub_mode(r.decision_snapshot)
        g = groups.setdefault(mode, _empty_metrics())
        g["decisions"] += 1
        op = str(r.operation or "hold").lower()
        if op in ("buy", "sell"):
            g["opens"] += 1
        if str(r.executed or "false").lower() == "true":
            g["executed"] += 1
        if op == "hold":
            g["hold_pct"] += 1
        conf = _row_confidence({"decision_snapshot": r.decision_snapshot})
        if conf is not None:
            g["_conf_sum"] += conf
            g["_conf_n"] += 1
        pnl = r.realized_pnl
        if pnl is not None:
            try:
                pnl = float(pnl)
            except (TypeError, ValueError):
                pnl = None
        if pnl is not None and abs(pnl) > 1e-12:
            g["realized_count"] += 1
            if pnl > 0:
                g["wins"] += 1
                g["_win_sum"] += pnl
            else:
                g["losses"] += 1
                g["_loss_sum"] += abs(pnl)

    for g in groups.values():
        g["hold_pct"] = round(g["hold_pct"] / g["decisions"], 4) if g["decisions"] else 0.0
        g["mean_confidence"] = round(g["_conf_sum"] / g["_conf_n"], 4) if g["_conf_n"] else None
        g["win_rate"] = round(g["wins"] / g["realized_count"], 4) if g["realized_count"] else None
        g["avg_pnl"] = round((g["_win_sum"] - g["_loss_sum"]) / g["realized_count"], 4) \
            if g["realized_count"] else None
        g["profit_factor"] = round(g["_win_sum"] / g["_loss_sum"], 4) \
            if g["_loss_sum"] > 0 else None
        for k in ("_conf_sum", "_conf_n", "_win_sum", "_loss_sum"):
            g.pop(k, None)
    return groups


def _empty_metrics() -> Dict[str, Any]:
    return {
        "decisions": 0, "opens": 0, "executed": 0, "hold_pct": 0.0,
        "mean_confidence": None, "realized_count": 0, "wins": 0, "losses": 0,
        "win_rate": None, "avg_pnl": None, "profit_factor": None,
        # 内部累加字段（聚合后弹出）
        "_conf_sum": 0.0, "_conf_n": 0, "_win_sum": 0.0, "_loss_sum": 0.0,
    }


def judge_deterioration(
    metrics: Dict[str, Dict[str, Any]],
    *,
    min_samples: Optional[int] = None,
) -> Dict[str, Any]:
    """
    灰度劣化判定：governed 对比 standard。

    status:
      - insufficient: governed realized 样本 < min_samples（未定论，维持现状）
      - ok:           governed 未显著劣化（继续灰度）
      - deteriorated: governed 命中率/盈亏比显著劣化（建议回滚 MLTO_AI_GOVERNED=0）
    """
    std = metrics.get(MODE_STANDARD, _empty_metrics())
    gov = metrics.get(MODE_GOVERNED, _empty_metrics())
    min_n = int(min_samples or _MIN_REALIZED_SAMPLES)
    reasons = []

    if gov["realized_count"] < min_n:
        return {
            "status": "insufficient",
            "governed_realized": gov["realized_count"],
            "min_samples": min_n,
            "reasons": [f"ai_governed 已实现样本 {gov['realized_count']} < {min_n}，暂不判定"],
        }

    deteriorated = False
    std_wr = std["win_rate"] if std["realized_count"] >= 3 else None
    gov_wr = gov["win_rate"]
    if std_wr is not None and gov_wr is not None:
        gap = std_wr - gov_wr
        if gap > _DETERIORATION_WIN_RATE_GAP:
            deteriorated = True
            reasons.append(
                f"命中率劣化 {gov_wr:.1%} < standard {std_wr:.1%} "
                f"(差 {gap:.1%} > {_DETERIORATION_WIN_RATE_GAP:.1%})"
            )
    std_pf = std["profit_factor"]
    gov_pf = gov["profit_factor"]
    if gov_pf is not None and std_pf is not None and std_pf > 0:
        if gov_pf < std_pf * (1 - _DETERIORATION_PF_DROP):
            deteriorated = True
            reasons.append(
                f"盈亏比劣化 {gov_pf:.2f} < standard {std_pf:.2f}×{(1 - _DETERIORATION_PF_DROP):.2f}"
            )

    return {
        "status": "deteriorated" if deteriorated else "ok",
        "standard": std,
        "governed": gov,
        "win_rate_gap": round((std_wr or 0) - (gov_wr or 0), 4),
        "reasons": reasons or ["未发现显著劣化"],
    }


def gray_verdict(db=None, *, days: Optional[int] = None) -> Dict[str, Any]:
    """一步式灰度对比：聚合 + 判定（供看板/运维脚本调用）。"""
    metrics = collect_gray_metrics(db, days=days)
    verdict = judge_deterioration(metrics)
    return {"metrics": metrics, "verdict": verdict}
