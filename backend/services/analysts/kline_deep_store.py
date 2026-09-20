# -*- coding: utf-8 -*-
"""K线深度分析**产物落库**（轮134）——把"烧了 LLM 却不留痕"的那一段接上。

## 实测缺陷
`KlineAnalyst` 每 24h 被调用 ~222 次（`full_auto_trading_service._warmup_analyst_reports`
→ `KlineAnalyst.analyze()`），但**返回值被直接丢弃**：
```
_analyst.analyze(_syms)          # ← 返回值没人接
logger.info("[AnalystWarmup] KlineAnalyst 缓存预热完成 …")
```
落库表 `alpha_analytics.kline_ai_analysis_logs` **0 行** ⇒ 六分析师里的
`kline_deep` 域只能报 `missing`（`analysts/scorers.py::score_kline_deep` 的实测结论）。

## 本模块做什么
把 `AnalystReport.signals` 里的**逐币结论**写成结构化行：
`analysis_result` = JSON（direction / score / summary / recommendation / detail 摘要），
供 `score_kline_deep` 直接解析（不再靠关键词猜方向）；同时保留 `detail` 原文便于复核。

开关：`KLINE_DEEP_PERSIST_ENABLED`（默认 true，回滚=置 false 即恢复"只预热不落库"）。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PRODUCER = "backend/services/analysts/kline_deep_store.py"
TABLE = "kline_ai_analysis_logs"

#: `KlineAnalyst` 的 signal 取值 → 数值方向（供 scorer 与混合打分消费）
_DIR_OF_SIGNAL = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0}


def enabled() -> bool:
    return (os.getenv("KLINE_DEEP_PERSIST_ENABLED", "true") or "true").strip().lower() in (
        "1", "true", "yes", "on")


def _resolve_ids(account_id: Optional[int], user_id: Optional[int]) -> tuple[int, int]:
    """解析 `(account_id, user_id)` —— 该表两列都 **NOT NULL**（实测无外键）。

    优先用调用方给的值；缺哪个就从 core 库 accounts 表解析（`accounts.user_id`）；
    仍解析不到时用 0（表示"系统/未指定"，而不是让它写失败丢产物 —— 用户明确要求不许因
    非业务原因把产物丢掉）。
    """
    acct, uid = account_id, user_id
    if acct is None or uid is None:
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import Account

            with SessionLocal() as db:
                q = db.query(Account)
                a = q.filter(Account.id == int(acct)).first() if acct else q.order_by(Account.id).first()
                if a is not None:
                    acct = int(acct) if acct is not None else int(getattr(a, "id", 0) or 0)
                    uid = int(uid) if uid is not None else int(getattr(a, "user_id", 0) or 0)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[KlineDeepStore] id 解析跳过: %s", exc)
    return int(acct or 0), int(uid or 0)


def persist_report(report: Any, *, account_id: Optional[int] = None,
                   user_id: Optional[int] = None, period: str = "multi",
                   model_used: str = "") -> int:
    """把一份 `AnalystReport` 的逐币结论落库；返回写入行数（失败不抛，记 warning）。"""
    if not enabled():
        logger.debug("[KlineDeepStore] 落库已停用（KLINE_DEEP_PERSIST_ENABLED=false）")
        return 0
    if report is None:
        return 0
    signals: List[Dict[str, Any]] = list(getattr(report, "signals", None) or [])
    if not signals:
        return 0
    _acct, _uid = _resolve_ids(account_id, user_id)
    summary = str(getattr(report, "summary", "") or "")[:400]
    rec = str(getattr(report, "recommendation", "") or "")[:300]
    ts = datetime.now()
    rows = []
    for s in signals:
        if not isinstance(s, dict) or not s.get("symbol"):
            continue
        sig = str(s.get("signal") or "neutral").lower()
        try:
            score = float(s.get("score") or 50)
        except Exception:  # noqa: BLE001
            score = 50.0
        payload = {
            "direction": _DIR_OF_SIGNAL.get(sig, 0.0),
            "signal": sig,
            "score": score,
            "summary": summary,
            "recommendation": rec,
            "detail": str(s.get("detail") or "")[:4000],
            "producer": PRODUCER,
        }
        rows.append({
            "user_id": _uid, "account_id": _acct,
            "symbol": str(s["symbol"]).upper()[:24], "period": str(period)[:16],
            "user_message": "kline_deep_analyst", "model_used": str(model_used or "")[:80],
            "prompt_snapshot": None,
            "analysis_result": json.dumps(payload, ensure_ascii=False)[:20000],
            "created_at": ts,
        })
    if not rows:
        return 0
    try:
        from sqlalchemy import text

        from backend.database.connection import analytics_engine

        with analytics_engine.begin() as c:
            c.execute(text(
                f"INSERT INTO {TABLE} (user_id, account_id, symbol, period, user_message, "
                f"model_used, prompt_snapshot, analysis_result, created_at) VALUES "
                f"(:user_id, :account_id, :symbol, :period, :user_message, :model_used, "
                f":prompt_snapshot, :analysis_result, :created_at)"), rows)
        logger.info("[KlineDeepStore] K线深度产物落库 %d 条（symbols=%s）",
                    len(rows), ",".join(r["symbol"] for r in rows)[:80])
        return len(rows)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[KlineDeepStore] 落库失败（不影响主链）: %s", exc)
        return 0


def latest_for_symbol(symbol: str, *, hours: float = 72.0) -> Optional[Dict[str, Any]]:
    """读某币最近一条结构化结论（供 scorer / 画布复核）。"""
    try:
        from sqlalchemy import text

        from backend.database.connection import analytics_engine

        with analytics_engine.connect() as c:
            row = c.execute(text(
                f"select analysis_result, created_at from {TABLE} "
                f"where symbol = :s and created_at >= now() - make_interval(secs => :secs) "
                f"order by id desc limit 1"),
                {"s": str(symbol).upper(), "secs": float(hours) * 3600}).fetchone()
        if not row:
            return None
        d = json.loads(row[0] or "{}")
        d["created_at"] = str(row[1])[:19]
        return d
    except Exception as exc:  # noqa: BLE001
        logger.debug("[KlineDeepStore] 读取失败: %s", exc)
        return None
