# -*- coding: utf-8 -*-
"""用**真实已平仓决策**补齐 RL 回放缓冲（[2026-10-03 ③ 回放样本去合成]）。

## 实测问题
`/api/learning/replay/stats` 长期是 `{"total": 302, "by_source": {"backtest": 2, "synthetic": 300}}`
—— **99% 是合成样本**，真实成交只有 2 条。RL agent（影子模式，384 步）几乎全在合成数据上训练，
"从真实交易中学习"这条链路名不副实。

## 本模块
把 `decision_snapshots`（分析库）里**真实已执行且已回填 pnl 的决策**转成 RL transition：
  · `action`：buy/long→1、sell/short→2、其余→3（与 `backtest_loop._seed_replay` 的语义一致）；
  · `reward` = `pnl_pct`（真实收益率，不用合成值）；
  · `state` = {confidence, tier, regime, source_lane}（真实决策上下文）；
  · `source` = **`live`**（真实成交），与 `synthetic` 明确区分。

可重复调用：按 (symbol, action, reward, created_at) 近似去重——同一行重复灌入不会重复计数
（缓冲表无唯一键，这里在写入前查一次）。

调用：`POST /api/learning/replay/seed-real`，或 L1 累积任务里调用 `seed_from_real_decisions()`。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _action_code(action: str, direction: str) -> int:
    a = f"{action or ''} {direction or ''}".lower()
    if "buy" in a or "long" in a:
        return 1
    if "sell" in a or "short" in a:
        return 2
    return 3


def seed_from_real_decisions(days: int = 180, limit: int = 1000,
                             source: str = "live") -> Dict[str, Any]:
    """把真实已平仓决策灌入 RL 回放缓冲。返回 {scanned, added, skipped, total_by_source}。

    [2026-10-03] 奖励护栏：`pnl_pct` 由 `pnl/margin` 得出，**保证金极小时会爆表**
    （实测出现 |reward|=1500 这类畸变值，把 avg_reward 拉到 7.26）⇒ 超过
    `MAX_ABS_REWARD`（默认 20 = ±2000%）的样本**不入库**，只计数（`implausible`），
    避免污染 RL 训练。
    """
    from backend.database.connection import AnalyticsSessionLocal
    from backend.services.learning_core.rl_core.replay_buffer import replay_buffer
    from sqlalchemy import text

    since = datetime.now(timezone.utc) - timedelta(days=int(days))
    db = AnalyticsSessionLocal()
    rows: List[Any] = []
    try:
        rows = db.execute(text("""
            SELECT symbol, action, direction, pnl_pct, pnl, confidence, tier,
                   regime_at_decision, source_lane, "timestamp"
            FROM decision_snapshots
            WHERE executed IS TRUE AND pnl_pct IS NOT NULL
              AND "timestamp" >= :since
            ORDER BY "timestamp" DESC LIMIT :lim
        """), {"since": since, "lim": int(limit)}).fetchall()
    except Exception as e:
        logger.warning("[ReplaySeed] 读取 decision_snapshots 失败: %s", e)
        return {"error": str(e)[:200], "scanned": 0, "added": 0}
    finally:
        db.close()

    replay_buffer.ensure_initialized()
    # [2026-10-03] 去重键必须带**决策时间戳**：早先用 (symbol, action, reward) 太粗，
    # 同币同向且 reward 相同（如多笔 0.0 平局）的真实交易会被误判为重复（实测 47 条只进 1 条）。
    existing: set = set()
    try:
        with replay_buffer._connect() as conn:  # noqa: SLF001（同包内使用）
            for r in conn.execute(
                "SELECT symbol, action, reward, "
                "coalesce(json_extract(state, '$.snap_ts'), '') FROM rl_transitions"
            ).fetchall():
                existing.add((str(r[0]), int(r[1]), round(float(r[2] or 0), 6), str(r[3] or "")))
    except Exception as e:
        logger.debug("[ReplaySeed] 去重集合读取失败（忽略）: %s", e)

    transitions: List[Dict[str, Any]] = []
    skipped = 0
    implausible = 0
    max_abs = float(__import__("os").getenv("RL_MAX_ABS_REWARD", "20") or 20)
    for r in rows:
        symbol, action, direction, pnl_pct, _pnl, conf, tier, regime, lane, ts = tuple(r)
        code = _action_code(str(action or ""), str(direction or ""))
        reward = float(pnl_pct or 0)
        if abs(reward) > max_abs:
            implausible += 1
            continue
        snap_ts = str(ts)[:19] if ts is not None else ""
        key = (str(symbol), code, round(reward, 6), snap_ts)
        if key in existing:
            skipped += 1
            continue
        existing.add(key)
        transitions.append({
            "symbol": symbol,
            "source": source,
            "state": {
                "confidence": float(conf or 0),
                "tier": str(tier or ""),
                "regime": str(regime or ""),
                "lane": str(lane or ""),
                "snap_ts": snap_ts,  # 去重键的一部分（真实决策时刻）
            },
            "action": code,
            "reward": reward,
            "next_state": None,
            "done": True,
            "lineage_id": None,
        })

    added = replay_buffer.add_batch(transitions) if transitions else 0
    stats = replay_buffer.stats()
    logger.info("[ReplaySeed] 真实样本灌入: scanned=%d added=%d skipped=%d implausible=%d by_source=%s",
                len(rows), added, skipped, implausible, stats.get("by_source"))
    return {"scanned": len(rows), "added": added, "skipped": skipped,
            "implausible": implausible,
            "total_by_source": stats.get("by_source"), "total": stats.get("total")}
