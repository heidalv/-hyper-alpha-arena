"""决策 block 模式学习 — 消费 DecisionSnapshot code_reason 统计高频拦截。"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Dict

from sqlalchemy.orm import Session

from ..backend_base import LearningBackend

logger = logging.getLogger(__name__)

# 内存聚合：tier -> reason -> count（进程内；LearningLoop 可扩展落库）
_block_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
_last_flush = 0.0


class BlockPatternLearningBackend(LearningBackend):
    name = "block_pattern_learning"
    priority = 145

    def should_trigger(self, db: Session, outcome) -> bool:
        if not self.enabled:
            return False
        meta = getattr(outcome, "metadata", None) or {}
        if meta.get("code_reason") or meta.get("block_reason"):
            return True
        if meta.get("was_blocked") or meta.get("gate_blocked"):
            return True
        return False

    def handle_outcome(self, db: Session, outcome) -> None:
        meta = getattr(outcome, "metadata", None) or {}
        reason = (
            meta.get("code_reason")
            or meta.get("block_reason")
            or meta.get("gate_reason")
            or "unknown_block"
        )
        tier = (getattr(outcome, "tier", None) or meta.get("tier") or "unknown").lower()
        key = f"{tier}:{str(reason)[:120]}"
        _block_stats[tier][str(reason)[:120]] += 1
        logger.info("[BlockPatternLearning] %s count=%d", key, _block_stats[tier][str(reason)[:120]])
        self._maybe_flush_to_memory(db, tier, str(reason)[:120])

    @staticmethod
    def _maybe_flush_to_memory(db: Session, tier: str, reason: str) -> None:
        global _last_flush
        now = time.time()
        if now - _last_flush < 300:
            return
        _last_flush = now
        try:
            from backend.database.models import StrategyMemory
            top = sorted(_block_stats.get(tier, {}).items(), key=lambda x: -x[1])[:5]
            if not top:
                return
            lesson = "; ".join(f"{r}×{c}" for r, c in top)
            mem = db.query(StrategyMemory).filter(
                StrategyMemory.strategy_id == "_global_",
                StrategyMemory.category == "block_pattern",
            ).first()
            if mem:
                mem.content = lesson
            else:
                mem = StrategyMemory(
                    strategy_id="_global_",
                    category="block_pattern",
                    content=lesson,
                    source="block_pattern_learning",
                )
                db.add(mem)
            db.commit()
            logger.info("[BlockPatternLearning] 已写入全局 block 模式: %s", lesson[:200])
        except Exception as exc:
            logger.debug("[BlockPatternLearning] flush 跳过: %s", exc)
            try:
                db.rollback()
            except Exception:
                pass

    @staticmethod
    def get_stats() -> Dict[str, Any]:
        return {tier: dict(reasons) for tier, reasons in _block_stats.items()}


# ─────────────────────────────────────────────────────────────────────────────
# [2026-10-03 补齐] 真实拦截模式聚合（`/api/hermes/block-patterns` 此前永远返回空）
#
# 实测根因（两条，都是设计缺陷）：
#   1. 本后端**从未注册进 LearningLoop**（全仓库只有 `hermes_routes` 读它的 `get_stats()`）
#      ⇒ `handle_outcome` 永不执行、`_block_stats` 恒空；
#   2. 即便执行，统计也是**纯内存**，进程一重启就清零（今天后端重启十余次）。
#   且上游也没记原因：`decision_snapshots.gate_blocks_json` 12,219 行**全空**、
#   `evaluate_verdict_json.reason/code_reason` 是空串 ⇒ 没有可消费的拦截原因。
#
# 但库里**有真实可用的证据**：`decision_snapshots`（分析库）11,990 条未执行决策、
# 其中 9,926 条带 `ai_reasoning` 自由文本理由；`source_lane` 区分车道。
# 本函数据此做**有据可查**的聚合（不改写任何数据、不编造原因）：
#   · 各车道/周期的决策数、执行数、**执行率**（master 车道 30 天 9926 次决策、执行 0 次即由此暴露）；
#   · 拦截层级分布（`evaluate_verdict_json.layer/rule`，这是结构化字段）；
#   · 理由分类计数（对 `ai_reasoning` 做**关键词归类**，映射表见 `_REASON_CATEGORIES`，
#     每类附一条原样摘录，便于人工核对）。
# 关键词归类是启发式：`classify_confidence` 字段标明命中率，未命中归入 `其它`。
# ─────────────────────────────────────────────────────────────────────────────

_REASON_CATEGORIES = [
    ("证据不足/观望", ("证据不足", "观望", "不参与", "缺乏", "等待")),
    ("方向不明/矛盾", ("方向不明", "中性", "不明", "矛盾", "冲突", "背离")),
    ("置信/强度不足", ("置信", "强度", "偏低", "不足", "仅", "弱")),
    ("硬否决", ("硬否决", "否决", "拦截", "禁开", "禁止")),
    ("连亏/回撤保护", ("连亏", "亏损", "回撤", "止损")),
    ("风控/上限/冷却", ("风控", "熔断", "冷却", "上限", "超额", "预算")),
    ("位置/区间不利", ("超买", "超卖", "分位", "追高", "天花板", "区间")),
]


def classify_reason(text: str) -> str:
    """把自由文本理由归入固定类别（首个命中的类别；无命中→其它）。"""
    t = str(text or "")
    for label, kws in _REASON_CATEGORIES:
        if any(k in t for k in kws):
            return label
    return "其它"


def compute_from_snapshots(days: int = 30, limit_reasons: int = 12) -> Dict[str, Any]:
    """从 `decision_snapshots` 聚合真实拦截模式（只读，不落库）。"""
    from backend.database.connection import AnalyticsSessionLocal
    from sqlalchemy import text

    out: Dict[str, Any] = {"window_days": int(days), "by_lane": [], "by_layer": [],
                           "reason_categories": [], "totals": {}, "examples": {}}
    db = AnalyticsSessionLocal()
    try:
        rows = db.execute(text(f"""
            SELECT coalesce(source_lane, 'unknown') AS lane,
                   coalesce(tier, 'unknown')        AS tier,
                   count(*)                          AS decisions,
                   count(*) FILTER (WHERE executed IS TRUE) AS executed
            FROM decision_snapshots
            WHERE "timestamp" > now() - interval '{int(days)} days'
            GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20
        """)).fetchall()
        tot_d = tot_e = 0
        for lane, tier, dec, exe in rows:
            dec, exe = int(dec), int(exe)
            tot_d += dec
            tot_e += exe
            out["by_lane"].append({"lane": lane, "tier": tier, "decisions": dec,
                                   "executed": exe,
                                   "exec_rate": round(exe / dec, 4) if dec else 0.0})
        out["totals"] = {"decisions": tot_d, "executed": tot_e,
                         "exec_rate": round(tot_e / tot_d, 4) if tot_d else 0.0,
                         "blocked": tot_d - tot_e}

        # 拦截层级（结构化字段；reason 为空是上游缺陷，已在报告里注明）
        try:
            layers = db.execute(text(f"""
                SELECT coalesce(evaluate_verdict_json->>'layer', 'unknown') AS layer,
                       coalesce(evaluate_verdict_json->>'rule', '')          AS rule,
                       count(*) AS n
                FROM decision_snapshots
                WHERE "timestamp" > now() - interval '{int(days)} days'
                  AND coalesce(evaluate_verdict_json->>'allowed', 'true') = 'false'
                GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10
            """)).fetchall()
            out["by_layer"] = [{"layer": r[0], "rule": r[1], "count": int(r[2])} for r in layers]
        except Exception as e:
            out["by_layer_error"] = str(e)[:120]

        # 理由分类（ai_reasoning 自由文本 → 关键词归类）
        # 注意：**不设 LIMIT**（此前写 LIMIT 400 会让分类计数只覆盖前 400 个高频文本，
        # 与真实 9,926 条差一个数量级 —— 数字必须覆盖全量才可信）
        reasons = db.execute(text(f"""
            SELECT coalesce(ai_reasoning, '') AS r, count(*) AS n
            FROM decision_snapshots
            WHERE "timestamp" > now() - interval '{int(days)} days'
              AND executed IS NOT TRUE AND coalesce(ai_reasoning, '') <> ''
            GROUP BY 1
        """)).fetchall()
        buckets: Dict[str, Dict[str, Any]] = {}
        for r, n in reasons:
            cat = classify_reason(r)
            b = buckets.setdefault(cat, {"category": cat, "count": 0, "example": ""})
            b["count"] += int(n)
            if not b["example"]:
                b["example"] = str(r)[:120]
        ordered = sorted(buckets.values(), key=lambda x: -x["count"])[:int(limit_reasons)]
        out["reason_categories"] = ordered
        out["examples"] = {b["category"]: b["example"] for b in ordered}
        out["classified_rows"] = sum(b["count"] for b in buckets.values())

        # [2026-10-03 ②] 按**前缀标签**统计（代码里强制 hold 处本来就会写 `arb_conflict:` /
        # `cycle_conflict:` / `data_gate:` …）—— 这是"master 为什么只会 hold"的**结构化**答案，
        # 不依赖关键词猜测；历史行也能算（无需回填）。
        try:
            tagged = db.execute(text(f"""
                SELECT (regexp_match(coalesce(ai_reasoning,''), '^\\s*\\[?([a-z][a-z0-9_]{{2,30}})\\]?\\s*[:：]'))[1] AS tag,
                       count(*) AS n
                FROM decision_snapshots
                WHERE "timestamp" > now() - interval '{int(days)} days'
                  AND executed IS NOT TRUE AND coalesce(ai_reasoning,'') <> ''
                GROUP BY 1 ORDER BY 2 DESC LIMIT 12
            """)).fetchall()
            out["by_tag"] = [{"tag": (r[0] or "(无标签)"), "count": int(r[1])} for r in tagged]
        except Exception as e:
            out["by_tag_error"] = str(e)[:120]
        return out
    except Exception as e:
        out["error"] = str(e)[:200]
        return out
    finally:
        db.close()


def get_stats_persistent() -> Dict[str, Any]:
    """给路由用：内存计数（若被注册）+ 真实聚合（always）。"""
    return {"in_memory": BlockPatternLearningBackend.get_stats(),
            "from_snapshots": compute_from_snapshots()}
