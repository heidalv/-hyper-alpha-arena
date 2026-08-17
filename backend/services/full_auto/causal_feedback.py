"""因果回灌闭环（2026-08-17 新增 Agent，审计缺口 #3）。

把「为什么输」变成下一轮决策约束：
- 从 trade_facts（真实交易事件流）聚合近窗口亏损模式 → 生成 tier/币种级约束
  （如：短线 tier 近 24h 净亏且胜率 <35% → 该 tier 开仓置信度要求 +10）；
- 写入 data/causal_constraints.json（带时间戳，可审计、可回滚）；
- MasterController 决策前加载约束并注入 prompt 上下文（constraints 文本）。

与 causal_analyzer（单笔诊断）互补：这里做的是「模式级 → 决策约束」的闭环。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CONSTRAINTS_PATH = os.path.join("data", "causal_constraints.json")
_lock = threading.Lock()

# 阈值
_TIER_MIN_SAMPLES = 6          # tier 级约束最少样本数
_TIER_BAD_WIN_RATE = 0.35      # 胜率低于此值视为差
_SYM_LOSS_STREAK = 3           # 币种连续亏损笔数


def build_constraints(db, hours: int = 24) -> List[Dict[str, Any]]:
    """从 trade_facts 聚合近 hours 小时的亏损模式，产出决策约束列表。"""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import text as _sa_text

    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=int(hours))
    constraints: List[Dict[str, Any]] = []
    try:
        rows = db.execute(_sa_text(
            """
            SELECT tier, symbol, outcome, pnl, ts
            FROM trade_facts
            WHERE ts >= :since
            ORDER BY ts
            """
        ), {"since": since}).mappings().all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[CausalFeedback] trade_facts 查询失败: %s", exc)
        return constraints
    if not rows:
        return constraints

    # ── tier 级 ──
    tier_rows: Dict[str, List[Dict]] = {}
    for r in rows:
        tier_rows.setdefault(str(r["tier"] or "short"), []).append(r)
    for tier, rs in tier_rows.items():
        n = len(rs)
        wins = sum(1 for r in rs if str(r["outcome"]) == "win")
        pnl = sum(float(r["pnl"] or 0) for r in rs)
        wr = wins / n if n else 0
        if n >= _TIER_MIN_SAMPLES and wr < _TIER_BAD_WIN_RATE and pnl < 0:
            constraints.append({
                "scope": "tier", "target": tier, "kind": "confidence_boost",
                "value": 10,
                "reason": f"{tier} tier 近{hours}h: {n}笔 胜率{wr:.0%} 净亏{pnl:.2f}",
                "ts": int(time.time()),
            })

    # ── 币种级连亏 ──
    sym_streak: Dict[str, int] = {}
    for r in rows:
        sym = str(r["symbol"] or "").upper()
        if not sym:
            continue
        if str(r["outcome"]) == "loss":
            sym_streak[sym] = sym_streak.get(sym, 0) + 1
        else:
            sym_streak[sym] = 0
        if sym_streak[sym] >= _SYM_LOSS_STREAK:
            constraints.append({
                "scope": "symbol", "target": sym, "kind": "cooldown",
                "value": 3600,
                "reason": f"{sym} 连续亏损 {sym_streak[sym]} 笔 → 当日冷却 1h",
                "ts": int(time.time()),
            })
            sym_streak[sym] = 0  # 记录一次后重置，避免重复

    return constraints


def persist_constraints(constraints: List[Dict[str, Any]]) -> None:
    with _lock:
        try:
            os.makedirs(os.path.dirname(CONSTRAINTS_PATH) or ".", exist_ok=True)
            with open(CONSTRAINTS_PATH, "w", encoding="utf-8") as f:
                json.dump({"updated_at": int(time.time()), "constraints": constraints}, f, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[CausalFeedback] 约束落盘失败: %s", exc)


def load_constraints(max_age_hours: float = 6.0) -> List[Dict[str, Any]]:
    try:
        with open(CONSTRAINTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        age = time.time() - float(data.get("updated_at") or 0)
        if age > max_age_hours * 3600:
            return []
        return list(data.get("constraints") or [])
    except Exception:  # noqa: BLE001
        return []


def constraints_text(max_age_hours: float = 6.0) -> str:
    """约束文本（注入 MasterController prompt 用）；无约束返回空串。"""
    cs = load_constraints(max_age_hours)
    if not cs:
        return ""
    lines = ["[因果回灌约束] 近窗口亏损模式约束（违反将降低决策置信度）："]
    for c in cs[:8]:
        lines.append(f"- {c.get('reason', '')}")
    return "\n".join(lines)


def rebuild(db, hours: int = 24) -> Dict[str, Any]:
    """一次性重建 + 落盘（由每小时调度任务调用）。"""
    cs = build_constraints(db, hours)
    persist_constraints(cs)
    logger.info("[CausalFeedback] 约束重建: %d 条", len(cs))
    return {"constraints": len(cs), "updated_at": int(time.time())}
