# -*- coding: utf-8 -*-
"""news_events 历史标注回填（2026-09-03，配合 p2-event-strategies / E5-5）。

修的是三个串成一条链的老 bug 留下的历史数据：

  1. `published_at` 全 NULL —— 采集器用 `datetime.fromisoformat` 解析 RSS 的 **RFC-2822**
     时间串（`'Fri, 14 Aug 2026 21:19:39 +0000'`），必然抛错 → 一律存 None。
     原始串完好地留在 `raw_data['published_at']` 里，这里按正确格式解析回填，**不是造数**。
  2. `impact_strength` 全 1 —— 启发式分支从不给 strength 赋值。现按修好的规则用**原标题**
     重算（确定性重放，同样不是造数），并回填 `affected_symbols`。
  3. `ai_summary` 分不清来源 —— 规则产出的摘要统一加 `[kw]` 前缀，便于日后区分
     哪些是关键词标注、哪些是 LLM 标注。

用法：
    python -m backend.scripts.backfill_news_annotations            # 全量
    python -m backend.scripts.backfill_news_annotations --dry-run  # 只统计不写
    python -m backend.scripts.backfill_news_annotations --limit 500

只回填 `confidence <= 0.35`（即启发式标注）的行；LLM 标注过的行原样不动。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _rows_to_fix(db, limit: int) -> List[Dict[str, Any]]:
    from sqlalchemy import text

    return [dict(r) for r in db.execute(text(
        "SELECT id, title, raw_data, published_at, impact_strength, impact_direction, "
        "affected_symbols, ai_summary, confidence "
        "FROM news_events WHERE COALESCE(confidence, 0) <= 0.35 ORDER BY id DESC LIMIT :lim"
    ), {"lim": int(limit)}).mappings().all()]


def run(*, dry_run: bool = False, limit: int = 100000) -> Dict[str, Any]:
    from sqlalchemy import text

    from backend.core.tenant import set_system_identity
    from backend.database.connection import MarketSessionLocal
    from backend.services.news_intelligence_service import news_intelligence, parse_pub_time

    set_system_identity()
    db = MarketSessionLocal()
    stats = {"scanned": 0, "pub_filled": 0, "strength_changed": 0, "symbols_filled": 0,
             "updated": 0, "pub_unparsable": 0, "dry_run": dry_run}
    try:
        rows = _rows_to_fix(db, limit)
        stats["scanned"] = len(rows)
        strength_hist: Dict[int, int] = {}
        for r in rows:
            raw = r.get("raw_data")
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except Exception:
                    raw = {}
            raw = raw if isinstance(raw, dict) else {}

            updates: Dict[str, Any] = {}
            if r.get("published_at") is None:
                pub = parse_pub_time(raw.get("published_at"))
                if pub is not None:
                    updates["published_at"] = pub
                    stats["pub_filled"] += 1
                elif raw.get("published_at"):
                    stats["pub_unparsable"] += 1

            impact = news_intelligence._heuristic_analyze({"title": r.get("title") or ""})
            strength_hist[impact.strength] = strength_hist.get(impact.strength, 0) + 1
            if int(r.get("impact_strength") or 0) != int(impact.strength):
                updates["impact_strength"] = int(impact.strength)
                stats["strength_changed"] += 1
            cur_syms = r.get("affected_symbols")
            if not cur_syms or cur_syms in ([], ["BTC"]):
                if impact.symbols and impact.symbols != ["BTC"]:
                    updates["affected_symbols"] = json.dumps(impact.symbols)
                    stats["symbols_filled"] += 1
            if not str(r.get("ai_summary") or "").startswith("[kw]"):
                updates["ai_summary"] = impact.summary
            if impact.duration:
                updates["impact_duration"] = impact.duration

            if not updates or dry_run:
                continue
            sets = ", ".join(f"{k} = :{k}" for k in updates)
            params = dict(updates)
            params["rid"] = r["id"]
            db.execute(text(f"UPDATE news_events SET {sets} WHERE id = :rid"), params)
            stats["updated"] += 1
        if not dry_run:
            db.commit()
        stats["strength_hist"] = dict(sorted(strength_hist.items()))
    except Exception as exc:
        db.rollback()
        stats["error"] = str(exc)[:400]
        logger.exception("[backfill_news] 失败")
    finally:
        db.close()
    return stats


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="news_events 历史标注回填")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=100000)
    args = ap.parse_args()
    out = run(dry_run=args.dry_run, limit=args.limit)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0 if not out.get("error") else 1


if __name__ == "__main__":
    sys.exit(main())
