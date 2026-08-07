"""OpenCode Context Pack — 组装 L1 事实供 plan/build agent 消费。"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PACK_DIR = os.path.join("data", "opencode_reports")


def build_context_pack(
    db,
    *,
    window: str = "24h",
    domain: str = "ai",
) -> Dict[str, Any]:
    from backend.services.strategy_runtime_report import get_or_build_runtime_report
    from backend.services.paper_pace_controller import paper_pace_controller
    from backend.database.models import StrategyMemory, OpenCodeInsightDB

    # P0-1a: SRR 竞态防护 — force_refresh 可能与 SRR tick 并发导致 tc=0
    # 增加重试逻辑：最多重试 3 次，间隔 10s，确保拿到非零报告
    runtime = {}
    for attempt in range(1, 4):
        runtime = get_or_build_runtime_report(
            db, window=window, domain=domain, force_refresh=True,
        ) or {}
        tc = int(runtime.get("total_closed") or 0)
        if tc > 0 or attempt >= 3:
            if tc == 0 and attempt >= 3:
                logger.warning(
                    "[ContextPack] SRR 重试 %d 次后仍为 0 笔已平仓（window=%s domain=%s），继续构建 pack（数据不足将由上层跳过分析）",
                    attempt, window, domain,
                )
            break
        logger.info(
            "[ContextPack] SRR 返回 0 笔已平仓（attempt=%d/%d），等待 10s 后重试…",
            attempt, 3,
        )
        import time as _time
        _time.sleep(10)

    memories: List[Dict[str, Any]] = []
    try:
        rows = (
            db.query(StrategyMemory)
            .filter(StrategyMemory.total_trades > 0)
            .order_by(StrategyMemory.total_trades.desc())
            .limit(10)
            .all()
        )
        for m in rows:
            memories.append({
                "strategy_id": m.strategy_id,
                "win_rate": float(m.win_rate or 0),
                "total_trades": int(m.total_trades or 0),
                "key_lessons": (m.key_lessons or [])[-5:],
                "successful_patterns": (m.successful_patterns or [])[:3],
                "failed_patterns": (m.failed_patterns or [])[:3],
            })
    except Exception as err:
        logger.debug("[ContextPack] memory: %s", err)

    open_issues: List[str] = []
    try:
        issues = (
            db.query(OpenCodeInsightDB)
            .filter(OpenCodeInsightDB.status == "open", OpenCodeInsightDB.severity.in_(("major", "critical")))
            .order_by(OpenCodeInsightDB.id.desc())
            .limit(10)
            .all()
        )
        open_issues = [f"#{i.id} {i.title}" for i in issues]
    except Exception:
        pass

    trades_summary = ""
    try:
        from backend.services.trade_memory_context import build_recent_trades_section
        trades_summary = build_recent_trades_section(db, limit=15) or ""
    except Exception:
        pass

    log_error_digest: Dict[str, Any] = {}
    try:
        from backend.services.log_digest_service import build_digest
        log_error_digest = build_digest(window_hours=24)
    except Exception as err:
        logger.debug("[ContextPack] log_digest: %s", err)

    arb_block: Dict[str, Any] = {}
    if domain in ("arb", "cross"):
        from backend.services.strategy_runtime_report import get_or_build_runtime_report
        arb_report = get_or_build_runtime_report(db, window=window, domain="arb") or {}
        arb_block = arb_report.get("arb") or {}

    # 白名单与 reviewer 硬规则单一来源对齐，避免误导 LLM 产出会被硬拒的无效提案。
    try:
        from backend.services.opencode_proposal_reviewer import WHITELIST_TUNING_KEYS
        _wl_keys = sorted(WHITELIST_TUNING_KEYS)
    except Exception:
        _wl_keys = [
            "master_reduce_min_loss_pct", "tier_max_hold_sec",
            "master_close_min_loss_pct_by_tier", "max_daily_trades",
            "maturity_max_warmup_relief", "maturity_global_n1", "maturity_global_n2",
        ]

    _min_closed = 5
    _training_block: Dict[str, Any] = {}
    try:
        from backend.services.training_phase_service import is_active, load_state, target_symbols

        if is_active():
            _min_closed = 3
            _training_block = {
                "active": True,
                "symbols": target_symbols(),
                "max_active_strategies": load_state().get("max_active_strategies", 10),
            }
    except Exception:
        pass

    _health_apis: Dict[str, Any] = {}
    try:
        from backend.services.health_snapshot_service import build_health_snapshot

        _health_apis = build_health_snapshot() or {}
    except Exception:
        pass

    # [2026-07-11 修复] plan agent 此前只拿到 whitelist_keys 的 key 名单，没有任何当前值/
    # 允许区间参照，导致算不出"基线±20%"的具体数字，实测大量 patch.value 输出成
    # "<need baseline>" 之类占位符，在解析阶段被判无效——提案有效率长期偏低。
    # 这里把每个白名单 key 的当前生效值 + schema min/max 一起注入，让 LLM 有据可算。
    _tuning_baseline: Dict[str, Any] = {}
    try:
        from backend.services.runtime_tuning_store import get_all_tuning, _DEFAULT_SCHEMA

        _all_tuning = get_all_tuning()
        for _k in _wl_keys:
            _entry = _all_tuning.get(_k, _DEFAULT_SCHEMA.get(_k))
            if isinstance(_entry, dict) and "value" in _entry:
                _tuning_baseline[_k] = {
                    "current": _entry.get("value"),
                    "min": _entry.get("min"),
                    "max": _entry.get("max"),
                }
            elif _entry is not None:
                # 嵌套结构（如 by_nature/tier_max_hold_sec）没有单一 current 值，
                # 原样给出供 LLM 参考字段级 patch，不提供 ±20% 自动换算。
                _tuning_baseline[_k] = {"current": _entry}
    except Exception as err:
        logger.debug("[ContextPack] tuning_baseline: %s", err)

    pack = {
        "window": window,
        "domain": domain,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_report": runtime,
        "data_quality": {
            "runtime_report_total_closed": int(runtime.get("total_closed") or 0),
            "runtime_report_win_rate": float(runtime.get("win_rate") or 0),
            "has_recent_trades": bool(trades_summary.strip()),
            "strategy_memories_n": len(memories),
            "open_major_issues_n": len(open_issues),
            "has_log_errors": bool(log_error_digest.get("has_log_errors")),
            "log_error_total_24h": int(log_error_digest.get("total_errors") or 0),
            "log_error_p0_count": int(log_error_digest.get("p0_count") or 0),
            "sufficient_for_analysis": int(runtime.get("total_closed") or 0) >= _min_closed,
        },
        "training_phase": _training_block,
        "health_apis_snapshot": _health_apis,
        "log_error_digest": log_error_digest,
        "pace_gear": paper_pace_controller.gear,
        "strategy_memories_top": memories,
        "recent_trades_summary": trades_summary[:4000],
        "arb": arb_block,
        "open_issues": open_issues,
        "rule_breaches": runtime.get("rule_breaches") or [],
        # 仅列 LLM 真正可 patch 的目标（v5_runtime_gates.json 已收敛为只读 legacy，移除）
        "whitelist_files": [
            "data/runtime_tuning.json",
            "data/decision_policies/master_close.yaml",
        ],
        "whitelist_keys": _wl_keys,
        "tuning_baseline": _tuning_baseline,
        "whitelist_policy_note": (
            "policy_yaml 仅允许 master_close 的字段级 patch，key 形如 "
            "'rule_id.field' 或 'master_close.yaml#rule_id.field'；"
            "禁止整文件 content 替换，禁止任何 .py 源码修改。"
        ),
    }

    # ── Hermes 自进化: 注入历史提案智慧（环 A: Wisdom → Context Pack）──
    hermes_wisdom_block = ""
    try:
        from backend.services.hermes_proposal_wisdom_engine import proposal_wisdom
        hermes_wisdom_block = proposal_wisdom.build_wisdom_context(limit=15)
        if hermes_wisdom_block:
            logger.debug("[ContextPack] Hermes wisdom injected: %d chars", len(hermes_wisdom_block))
    except Exception as herr:
        logger.debug("[ContextPack] Hermes wisdom skip: %s", herr)

    # ── 阶段 5.2: 统一账户视图（AI + 套利 合并敞口）──
    # 让 opencode 看到跨系统真实资金分布，便于闭环验证 + 资金冲突检测
    try:
        from backend.services.unified_account_service import unified_account_service
        _accounts_block: Dict[str, Any] = {"accounts": [], "combined_exposure": {}}
        # 列出所有 paper 账户（AI + 套利）
        for view in unified_account_service.list_all_paper_accounts(db):
            _accounts_block["accounts"].append(view.to_dict())
        # 跨系统合并敞口（取第一个 AI + 第一个套利）
        _ai_ids = [a["id"] for a in _accounts_block["accounts"] if a["scope"] == "ai"]
        _arb_ids = [a["id"] for a in _accounts_block["accounts"] if a["scope"] == "arbitrage"]
        if _ai_ids or _arb_ids:
            _exposure = unified_account_service.get_combined_exposure(
                db,
                ai_account_id=_ai_ids[0] if _ai_ids else None,
                arbitrage_account_id=_arb_ids[0] if _arb_ids else None,
            )
            _accounts_block["combined_exposure"] = _exposure.to_dict()
        pack["unified_accounts"] = _accounts_block
    except Exception as err:
        logger.debug("[ContextPack] unified_accounts: %s", err)
        pack["unified_accounts"] = {"error": str(err)}

    # Hermes wisdom 注入（环 A）
    if hermes_wisdom_block:
        pack["hermes_wisdom"] = hermes_wisdom_block

    return pack


def save_context_pack(pack: Dict[str, Any]) -> str:
    os.makedirs(PACK_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(PACK_DIR, f"context_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False, indent=2)
    return path


# ══════════════════════════════════════════════════════
#  Phase 6: 交易叙事构建 + 扩展模式上下文
# ══════════════════════════════════════════════════════

def build_trade_narrative_section(db, limit: int = 20) -> str:
    """
    从最近N笔交易中提取高层叙事，供 OpenCode 分析时使用。
    叙事聚焦于：AI犯了什么类型的错误、在哪个品种/方向上反复出错、有没有进步。
    """
    from backend.database.models import StrategyTrade
    from datetime import timedelta

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        recent = (
            db.query(StrategyTrade)
            .filter(
                StrategyTrade.status == "closed",
                StrategyTrade.closed_at >= cutoff,
                ~StrategyTrade.strategy_id.like("rebate_%"),
            )
            .order_by(StrategyTrade.closed_at.desc())
            .limit(limit)
            .all()
        )

        if not recent:
            return ""

        # 统计聚合
        total = len(recent)
        wins = [t for t in recent if (t.pnl or 0) > 0]
        losses = [t for t in recent if (t.pnl or 0) < 0]
        total_pnl = sum(t.pnl or 0 for t in recent)
        win_rate = len(wins) / total if total > 0 else 0

        # 按symbol聚合
        by_symbol: Dict[str, Dict] = {}
        for t in recent:
            sym = t.symbol or "?"
            if sym not in by_symbol:
                by_symbol[sym] = {"count": 0, "wins": 0, "pnl": 0.0}
            by_symbol[sym]["count"] += 1
            if (t.pnl or 0) > 0:
                by_symbol[sym]["wins"] += 1
            by_symbol[sym]["pnl"] += float(t.pnl or 0)

        # 按平仓原因聚合
        close_reasons: Dict[str, int] = {}
        for t in losses:
            ctx = t.decision_context if isinstance(t.decision_context, dict) else {}
            reason = str(ctx.get("close_reason") or "unknown")[:30]
            close_reasons[reason] = close_reasons.get(reason, 0) + 1

        lines = [
            "### 📊 24h交易叙事",
            f"- 总交易: {total}笔 | 胜率: {win_rate:.0%} | 净PnL: ${total_pnl:+.2f}",
            f"- 盈利: {len(wins)}笔 | 亏损: {len(losses)}笔",
        ]

        # 最大亏损品种
        worst_symbols = sorted(by_symbol.items(), key=lambda x: x[1]["pnl"])[:3]
        if worst_symbols:
            lines.append("- 最大亏损品种:")
            for sym, stats in worst_symbols:
                if stats["pnl"] < 0:
                    lines.append(
                        f"  {sym}: {stats['count']}笔, "
                        f"胜{stats['wins']}/{stats['count']}, "
                        f"PnL ${stats['pnl']:+.2f}"
                    )

        # 主要亏损原因
        top_reasons = sorted(close_reasons.items(), key=lambda x: x[1], reverse=True)[:3]
        if top_reasons:
            lines.append("- 主要平仓原因（亏损）:")
            for reason, count in top_reasons:
                lines.append(f"  {reason}: {count}笔")

        return "\n".join(lines)
    except Exception as exc:
        logger.debug("[ContextPack] trade_narrative: %s", exc)
        return ""


def build_context_pack_extended(db, window: str = "24h", domain: str = "ai") -> Dict[str, Any]:
    """
    扩展模式 context pack：在基础 pack 之上追加交易叙事 + 长期K线摘要。
    目标利用 OpenCode 128K 上下文窗口，从 ~1.5K 提升到 ~23K tokens。
    """
    pack = build_context_pack(db, window=window, domain=domain)

    # 追加交易叙事
    narrative = build_trade_narrative_section(db, limit=30)
    if narrative:
        pack["trade_narrative"] = narrative

    # 追加扩展K线摘要（如果 unified_data_pool 可用）
    try:
        from backend.services.unified_data_pool import unified_data_pool
        kline_extended = {}
        # 从 runtime_report 中获取活跃 symbol
        runtime = pack.get("runtime_report") or {}
        symbols_active = list((runtime.get("symbol_perf") or {}).keys())[:5]
        if not symbols_active:
            symbols_active = ["BTC", "ETH"]
        for sym in symbols_active:
            try:
                bars = unified_data_pool.get_kline_series(sym, interval="1h", limit=72)
                if bars:
                    summary = f"{sym} 72h: O={bars[0].open:.2f} → C={bars[-1].close:.2f} "
                    summary += f"H={max(b.high for b in bars):.2f} L={min(b.low for b in bars):.2f} "
                    # 简单趋势斜率
                    if len(bars) >= 24:
                        first_mid = sum(b.close for b in bars[:8]) / 8
                        last_mid = sum(b.close for b in bars[-8:]) / 8
                        change_pct = (last_mid - first_mid) / first_mid * 100 if first_mid else 0
                        summary += f"趋势: {change_pct:+.1f}%"
                    kline_extended[sym] = summary
            except Exception:
                pass
        if kline_extended:
            pack["kline_extended"] = kline_extended
    except Exception as err:
        logger.debug("[ContextPack] kline_extended: %s", err)

    pack["_extended_mode"] = True
    return pack
