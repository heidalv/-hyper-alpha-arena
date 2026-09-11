# -*- coding: utf-8 -*-
"""E5-5 新闻驱动避险（v3 方向 4，p2-event-strategies）。

方案原文：`高影响新闻 → 降杠杆 / 对冲窗口`。

与 E5-2/E5-3 不同，本策略的**主产物是一个风险闸门**（避险窗口内建议降杠杆），
方向性信号只是副产物：负面强新闻 → 短期偏空 + 开避险窗；正面强新闻 → 短期偏多、不开窗。
影子期照样把方向信号写进 signal_ledger，让「新闻是否真有方向性 edge」被同一套 KPI 检验；
`hedge_window()` 则给 RiskEngine / PositionConstruction 消费（Phase 3 才接线）。

**标注质量前提（必须读）**：本库 `news_events` 的标注长期由关键词启发式产出，
不是 LLM —— `confidence` 恒 0.3、`ai_summary` 带 `[kw]` 前缀即是标记。
2026-09-03 已修三处 bug（`published_at` RFC-2822 解析、启发式 strength 恒 1、桥接阈值 7 超出
1–5 量纲）并回填历史，但**强度分层仍是词表打出来的，不是语义理解**。
因此本策略的阈值按当前量纲标定，`quality` 字段如实报告标注来源占比；
LLM 标注接上后必须用 `backtest()` 重标定阈值，不可沿用。

规则：
  1. 取 `news_events`，时间轴优先 `published_at`（已回填），缺失回落 `created_at`；
  2. 触发：`impact_strength ≥ min_strength`（默认 4，量纲 1–5）且 `|impact_direction| ≥ min_direction`（默认 0.5）；
  3. 标的：`affected_symbols`（已回填真实币种），为空 → BTC（当全市场解读）；
  4. 方向：负面 → −1 且开 `hedge_hours`（默认 6h）避险窗；正面 → +1，窗口 0；
  5. 同币冷却 `cooldown_min`（默认 60min），避免同一事件被多家媒体重复报道刷屏。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.services.strategies.event.base import (
    EventShadowStrategy,
    EventSignal,
    base_symbol,
    env_float,
    env_int,
    now_ms,
    register_strategy,
)

logger = logging.getLogger(__name__)

STRATEGY_ID = "e5_5_news_hedge"


def _market_db():
    from backend.database.connection import MarketSessionLocal

    return MarketSessionLocal()


def _to_ms(dt: Any) -> Optional[int]:
    """TIMESTAMP → epoch ms。naive 值按系统本地时区解释（与 events/bridge.py 同口径）。"""
    if dt is None:
        return None
    if isinstance(dt, (int, float)):
        return int(dt if dt > 1e11 else dt * 1000)
    if isinstance(dt, datetime):
        return int((dt.astimezone() if dt.tzinfo is None else dt).timestamp() * 1000)
    return None


def _symbols_of(raw: Any) -> List[str]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = [raw]
    out: List[str] = []
    for s in raw or []:
        b = base_symbol(s)
        if b and b not in out:
            out.append(b)
    return out[:6]


def load_news(since_ms: int, until_ms: int, *, min_strength: int, limit: int = 5000) -> List[Dict[str, Any]]:
    from sqlalchemy import text

    db = _market_db()
    try:
        rows = db.execute(text(
            "SELECT id, source, title, url, published_at, created_at, impact_direction, impact_strength, "
            "impact_duration, affected_symbols, event_category, confidence, ai_summary "
            "FROM news_events WHERE COALESCE(impact_strength, 0) >= :ms "
            "AND COALESCE(published_at, created_at) >= :lo AND COALESCE(published_at, created_at) <= :hi "
            "ORDER BY COALESCE(published_at, created_at) LIMIT :lim"
        ), {
            "ms": int(min_strength),
            "lo": datetime.fromtimestamp(since_ms / 1000).astimezone().replace(tzinfo=None),
            "hi": datetime.fromtimestamp(until_ms / 1000).astimezone().replace(tzinfo=None),
            "lim": int(limit),
        }).mappings().all()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("[%s] news_events 读取失败: %s", STRATEGY_ID, exc)
        return []
    finally:
        db.close()


def annotation_quality(days: int = 30) -> Dict[str, Any]:
    """报告标注来源构成：关键词规则 vs LLM。E5-5 的所有结论都要挂这个上下文。"""
    from sqlalchemy import text

    db = _market_db()
    try:
        r = db.execute(text(
            "SELECT COUNT(*) AS n, "
            "SUM(CASE WHEN COALESCE(confidence,0) <= 0.35 THEN 1 ELSE 0 END) AS kw, "
            "SUM(CASE WHEN published_at IS NOT NULL THEN 1 ELSE 0 END) AS has_pub, "
            "SUM(CASE WHEN COALESCE(impact_strength,0) >= 4 THEN 1 ELSE 0 END) AS strong "
            "FROM news_events WHERE created_at >= now() - make_interval(days => :d)"
        ), {"d": int(days)}).mappings().first()
        n = int(r["n"] or 0) if r else 0
        kw = int(r["kw"] or 0) if r else 0
        return {
            "days": days, "n": n, "keyword_annotated": kw,
            "llm_annotated": n - kw,
            "keyword_share": round(kw / n, 4) if n else None,
            "has_published_at": int(r["has_pub"] or 0) if r else 0,
            "strength_ge_4": int(r["strong"] or 0) if r else 0,
            "note": ("标注全部来自关键词规则，强度分层无语义理解；接入 LLM 标注后需用 backtest() 重标定阈值"
                     if n and kw == n else "含 LLM 标注，阈值可按 backtest 结果调整"),
        }
    except Exception as exc:
        return {"error": str(exc)[:200]}
    finally:
        db.close()


class NewsHedgeStrategy(EventShadowStrategy):
    strategy_id = STRATEGY_ID
    description = "高影响新闻 → 避险窗口 + 短期方向信号（负面 6h / 正面 3h）"
    default_horizon_h = 6.0

    def __init__(self):
        self.min_strength = env_int("E5_NEWS_MIN_STRENGTH", 4)       # 量纲 1–5
        self.min_direction = env_float("E5_NEWS_MIN_DIRECTION", 0.5)
        self.hedge_hours = env_float("E5_NEWS_HEDGE_HOURS", 6.0)
        self.pos_horizon_h = env_float("E5_NEWS_POS_HORIZON_H", 3.0)
        self.cooldown_min = env_int("E5_NEWS_COOLDOWN_MIN", 60)
        self.hedge_leverage_mult = env_float("E5_NEWS_HEDGE_LEV_MULT", 0.5)

    def detect(self, *, since_ms: int, until_ms: int, limit: int = 5000,
               notes: Optional[List[str]] = None) -> List[EventSignal]:
        notes = notes if notes is not None else []
        rows = load_news(since_ms, until_ms, min_strength=self.min_strength, limit=limit * 4)
        if not rows:
            notes.append(f"窗口内无 strength ≥ {self.min_strength} 的新闻")
            return []

        q = annotation_quality(days=max(7, int((until_ms - since_ms) / 86400000) or 7))
        if q.get("keyword_share") == 1.0:
            notes.append("标注 100% 来自关键词规则（非 LLM），阈值为当前量纲下的经验值")

        out: List[EventSignal] = []
        last_by_symbol: Dict[str, int] = {}
        weak_dir = 0
        for r in rows:
            try:
                direction_raw = float(r.get("impact_direction") or 0.0)
            except (TypeError, ValueError):
                continue
            if abs(direction_raw) < self.min_direction:
                weak_dir += 1
                continue
            ts = _to_ms(r.get("published_at")) or _to_ms(r.get("created_at"))
            if not ts or ts < since_ms or ts > until_ms:
                continue
            strength = int(r.get("impact_strength") or 0)
            syms = _symbols_of(r.get("affected_symbols")) or ["BTC"]
            negative = direction_raw < 0
            for sym in syms:
                if ts - last_by_symbol.get(sym, -10**9) < self.cooldown_min * 60 * 1000:
                    continue
                last_by_symbol[sym] = ts
                out.append(EventSignal(
                    ts_ms=ts,
                    symbol=sym,
                    direction=-1 if negative else 1,
                    horizon_h=self.hedge_hours if negative else self.pos_horizon_h,
                    strength=round(min(10.0, strength * 2.0 * abs(direction_raw)), 2),
                    confidence=round(min(0.65, 0.35 + 0.05 * strength + 0.1 * abs(direction_raw)), 3),
                    reason=f"{'负面' if negative else '正面'}新闻 强度{strength}/5 方向{direction_raw:+.2f}"
                           f"：{str(r.get('title') or '')[:80]}",
                    payload={
                        "strategy": self.strategy_id, "news_id": r.get("id"),
                        "news_source": r.get("source"), "url": r.get("url"),
                        "impact_strength": strength, "impact_direction": round(direction_raw, 3),
                        "event_category": r.get("event_category"),
                        "annotation": ("keyword" if float(r.get("confidence") or 0) <= 0.35 else "llm"),
                        "hedge_window_h": (self.hedge_hours if negative else 0.0),
                        "leverage_mult": (self.hedge_leverage_mult if negative else 1.0),
                    },
                ))
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                notes.append(f"命中数达上限 {limit}，已截断")
                break

        if weak_dir:
            notes.append(f"{weak_dir} 条强度达标但 |direction| < {self.min_direction} 被过滤")
        out.sort(key=lambda s: s.ts_ms)
        return out

    # ---------------- 风险闸门（Phase 3 由 RiskEngine 消费）----------------
    def hedge_window(self) -> Dict[str, Any]:
        """当前是否处于新闻避险窗口 → {active, until_ms, leverage_mult, reasons}。

        只读、无副作用。Phase 3 接线时由 PositionConstruction 把 `leverage_mult` 乘进目标仓位；
        影子期只在看板与日报里展示，不影响任何下单。
        """
        until = now_ms()
        since = until - int(self.hedge_hours * 3600 * 1000)
        try:
            signals = self.detect_scorable(since_ms=since, until_ms=until, limit=200)
        except Exception as exc:
            return {"active": False, "error": str(exc)[:200]}
        active = [s for s in signals
                  if s.direction < 0 and s.ts_ms + s.horizon_h * 3600 * 1000 > until]
        if not active:
            return {"active": False, "leverage_mult": 1.0, "reasons": []}
        end = max(int(s.ts_ms + s.horizon_h * 3600 * 1000) for s in active)
        return {
            "active": True,
            "until_ms": end,
            "remaining_min": round((end - until) / 60000, 1),
            "leverage_mult": self.hedge_leverage_mult,
            "n_events": len(active),
            "symbols": sorted({s.symbol for s in active}),
            "reasons": [s.reason for s in active[:5]],
            "applied": False,
            "note": "影子期只展示不生效；Phase 3 由 RiskEngine/PositionConstruction 消费",
        }

    def status(self) -> Dict[str, Any]:
        out = super().status()
        out["annotation_quality"] = annotation_quality()
        out["hedge_window"] = self.hedge_window()
        return out


def build() -> NewsHedgeStrategy:
    return NewsHedgeStrategy()


register_strategy(STRATEGY_ID, build)
