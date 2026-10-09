# -*- coding: utf-8 -*-
"""消融周报 —— 臂(A0/A1/A2/A3) × regime × ISO周 的 IC/命中率/头部价差。

臂定义（对齐设计 §3.5 消融矩阵，从 score_log 重建）：
    A0 = 纯通道A（数值因子排序）
    A1 = A0 + thesis（thesis.present 子集上的 A0 表现——thesis 特征存在性消融）
    A2 = +LLM综合器（通道B分数）
    A3 = 全开（融合分）
产出：hybrid_ablation_report.json（供周一 08:00 周报任务与 /hybrid/report 端点消费）。
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict

import numpy as np

from backend.services.hybrid_scoring import config

logger = logging.getLogger(__name__)


def _rank_ic(xs, ys) -> float:
    if len(xs) < 5:
        return float("nan")
    rx = np.argsort(np.argsort(xs)) + 1.0
    ry = np.argsort(np.argsort(ys)) + 1.0
    rx = (rx - rx.mean()) / (rx.std() + 1e-12)
    ry = (ry - ry.mean()) / (ry.std() + 1e-12)
    return float((rx * ry).mean())


def weekly_report(weeks: int = 4) -> Dict[str, object]:
    path = config.score_log_path()
    if not path.exists():
        return {"ok": False, "error": "no_log"}
    # (week, regime, arm) -> 收集器
    buckets: Dict[tuple, Dict[str, list]] = defaultdict(
        lambda: {"xs": [], "ys": [], "hits": []})

    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        o = e.get("outcome") or {}
        y = o.get("ret_24h")
        if y is None:
            continue
        y = float(y)
        iso = str(e.get("iso") or "")
        try:
            week = datetime.fromisoformat(iso).isocalendar()
            week_key = f"{week[0]}-W{week[1]:02d}"
        except Exception:
            week_key = iso[:7]
        regime = str(e.get("regime") or "unknown")
        arms = e.get("arm_fields") or {}
        for arm in ("A0", "A1", "A2", "A3"):
            v = arms.get(arm)
            if v is None:
                continue
            b = buckets[(week_key, regime, arm)]
            b["xs"].append(float(v))
            b["ys"].append(y)
            b["hits"].append(1 if y > 0 else 0)
        # A1 历史回填：真实 A1 分数出现（流B上线）前，用 thesis 子集上的 A0 代理
        if arms.get("A1") is None and (e.get("thesis") or {}).get("present") and arms.get("A0") is not None:
            b = buckets[(week_key, regime, "A1")]
            b["xs"].append(float(arms["A0"]))
            b["ys"].append(y)
            b["hits"].append(1 if y > 0 else 0)

    table = []
    for (week, regime, arm), b in sorted(buckets.items()):
        if len(b["xs"]) < 5:
            continue
        xs, ys = np.asarray(b["xs"]), np.asarray(b["ys"])
        top_q = xs >= np.quantile(xs, 0.8)
        bot_q = xs <= np.quantile(xs, 0.2)
        table.append({
            "week": week, "regime": regime, "arm": arm, "n": len(b["xs"]),
            "rank_ic": round(_rank_ic(xs, ys), 4),
            "hit_rate": round(float(np.mean(b["hits"])), 3),
            "top20_mean_ret": round(float(ys[top_q].mean()), 5) if top_q.any() else None,
            "bottom20_mean_ret": round(float(ys[bot_q].mean()), 5) if bot_q.any() else None,
        })

    ic_stats = {}
    try:
        ic_stats = json.loads(config.ic_stats_path().read_text(encoding="utf-8"))
    except Exception:
        pass
    fused_ic = None
    for row in table:
        if row["arm"] == "A3":
            fused_ic = row["rank_ic"]
            break
    report = {
        "ok": True, "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": table,
        "ic_stats": ic_stats,
        "promotion_hint": {
            "fused_ic_recent": fused_ic,
            "threshold": config.ic_entry_threshold(),
            "note": "融合臂滚动 RankIC ≥ 阈值时【建议】提交 ROI 问责门复核 fusion 晋级；"
                    "本报告不自动切换任何模式。",
        },
    }
    config.report_path().write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[HybridScore.report] 周报生成 rows=%d → %s", len(table), config.report_path())
    return report
