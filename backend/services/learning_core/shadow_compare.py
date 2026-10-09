# -*- coding: utf-8 -*-
"""[工作流②③] 影子 vs 真实管线 对比报表（只读，2026-10-04）。

## 数据来源（实测）
账本 `data/learning_core.db` → `evolution_lineage`：
  · `source='rl_shadow'`        —— RL 影子决策（`ShadowDecisionService.decide`）
  · `source='vol_target_shadow'`—— 目标波动率影子（`shadow_eval`，已接入开仓路径）
两处接线位置：`paper_trading_engine.py` 开仓账本钩子之后（[2026-10-04 工作流②③]）。

## 诚实原则（避免"看着有报表、其实没数据"）
· 样本不足时**明确输出 `insufficient_samples` 与门槛**，不产出任何比率/结论；
· 比率一律带 `n`；不插值、不外推、不用其它来源顶替；
· 爆仓/回撤对比需要**持仓结果**（影子决策之后的盈亏），在成交积累前无法计算 —— 报表如实标注"待数据"。

## 回滚/开关
无副作用（纯读）；不写库、不改配置。开关仅用于限制读取量：`SHADOW_REPORT_LIMIT`（默认 500）。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MIN_RL_SAMPLES = int(os.getenv("SHADOW_RL_MIN_SAMPLES", "30") or 30)
MIN_VT_SAMPLES = int(os.getenv("SHADOW_VT_MIN_SAMPLES", "30") or 30)


def _ledger_path() -> str:
    return os.getenv("LEARNING_CORE_DB", r"data\learning_core.db")


def _rows(source: str, limit: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        con = sqlite3.connect(_ledger_path())
        cur = con.execute(
            "SELECT lineage_id, symbol, payload, metrics, status, created_at "
            "FROM evolution_lineage WHERE source=? ORDER BY rowid DESC LIMIT ?",
            (source, int(limit)),
        )
        for lid, sym, payload, metrics, status, created in cur.fetchall():
            try:
                p = json.loads(payload) if payload else {}
            except Exception:
                p = {}
            try:
                m = json.loads(metrics) if metrics else {}
            except Exception:
                m = {}
            out.append({"lineage_id": lid, "symbol": sym, "payload": p,
                        "metrics": m, "status": status, "created_at": created})
        con.close()
    except Exception as exc:
        logger.debug("[ShadowReport] 读取 %s 失败: %s", source, exc)
    return out


def vol_target_report(limit: Optional[int] = None) -> Dict[str, Any]:
    """②：影子目标波动率 vs 实际仓位。"""
    lim = int(limit or os.getenv("SHADOW_REPORT_LIMIT", "500") or 500)
    rows = _rows("vol_target_shadow", lim)
    out: Dict[str, Any] = {
        "workflow": "② 目标波动率影子",
        "n": len(rows),
        "min_samples": MIN_VT_SAMPLES,
        "note": "影子只计算不入账；σ̂ 用 4h 收益、截断 [5%,200%]",
    }
    if len(rows) < MIN_VT_SAMPLES:
        out.update({
            "status": "insufficient_samples",
            "hint": f"需 {MIN_VT_SAMPLES} 条影子记录（开仓时自动写入），当前 {len(rows)} 条 —— 需恢复交易会话后积累",
        })
        return out
    scale = [r["payload"].get("vol_target_scale") for r in rows if r["payload"].get("vol_target_scale")]
    sig = [r["payload"].get("sigma_annual") for r in rows if r["payload"].get("sigma_annual")]
    theo = [r["payload"].get("theoretical_notional") for r in rows if r["payload"].get("theoretical_notional")]
    act = [r["payload"].get("actual_notional") for r in rows if r["payload"].get("actual_notional")]
    over = [r for r in rows if r["payload"].get("over_budget")]
    n = lambda xs: len(xs)  # noqa: E731
    out.update({
        "status": "ok",
        "sigma_annual_median": round(sorted(sig)[len(sig) // 2], 4) if sig else None,
        "scale_median": round(sorted(scale)[len(scale) // 2], 4) if scale else None,
        "theoretical_notional_median": round(sorted(theo)[len(theo) // 2], 2) if theo else None,
        "actual_notional_median": round(sorted(act)[len(act) // 2], 2) if act else None,
        "over_budget_ratio": round(len(over) / len(rows), 4),
        "counts": {"scale": n(scale), "sigma": n(sig), "theoretical": n(theo), "actual": n(act)},
        "pending": ["爆仓次数对比", "最大回撤对比", "夏普对比 —— 需影子决策后的持仓结果"],
    })
    return out


def rl_report(limit: Optional[int] = None) -> Dict[str, Any]:
    """③：RL 影子建议 vs 真实决策（动作一致率）。"""
    lim = int(limit or os.getenv("SHADOW_REPORT_LIMIT", "500") or 500)
    rows = _rows("rl_shadow", lim)
    out: Dict[str, Any] = {
        "workflow": "③ RL 影子 vs 真实管线",
        "n": len(rows),
        "min_samples": MIN_RL_SAMPLES,
    }
    if len(rows) < MIN_RL_SAMPLES:
        out.update({
            "status": "insufficient_samples",
            "actions_seen": sorted({str(r["payload"].get("action_name")) for r in rows if r["payload"].get("action_name")}),
            "hint": f"需 {MIN_RL_SAMPLES} 条 rl_shadow 血缘（开仓时自动写入），当前 {len(rows)} 条",
        })
        return out
    from collections import Counter

    cnt = Counter(str(r["payload"].get("action_name")) for r in rows)
    out.update({
        "status": "ok",
        "action_distribution": dict(cnt),
        "pending": ["与真实决策的动作一致率", "按前向收益比较两臂优劣（复用工作流① 的标注）"],
    })
    return out


def combined(limit: Optional[int] = None) -> Dict[str, Any]:
    return {"vol_target": vol_target_report(limit), "rl": rl_report(limit)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(combined(), ensure_ascii=False, indent=1))
