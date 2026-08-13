"""MidLongHealthReport — 中长线健康视图（阶段三 C1 可观测性）。

集中回答"中长线激活改造是否见效"：
- 每 tier（mid=中线 swing / long=长线 trend_follow）滚动胜率 / 净期望 / 笔数；
- 开仓活跃度：lookback 内开仓笔数、日均开仓（判断"是否还在停摆"）；
- 长线周开单 vs 上限（是否触顶）；
- 当前生效的开仓门槛（runtime_tuning by_nature，判断门槛是否已校准）；
- 预算利用率（各层 cap/used/闲置，判断预算是否被真正使用）。

全部只读聚合，不改变任何交易状态。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_TIER_NATURES = {
    "mid": ("swing",),
    "long": ("trend_follow", "position"),
}


def _tier_trade_stats(db, lookback_days: int, natures: tuple, account_id: Optional[int]) -> Dict[str, Any]:
    from sqlalchemy import text

    params: Dict[str, Any] = {"days": lookback_days}
    nature_list = list(natures)
    # 构造 IN 占位
    in_clause = ",".join(f":n{i}" for i in range(len(nature_list)))
    for i, n in enumerate(nature_list):
        params[f"n{i}"] = n
    acct_clause = ""
    if account_id is not None:
        acct_clause = " AND account_id = :acct "
        params["acct"] = account_id

    sql = text(
        f"""
        SELECT side, entry_price, close_price
        FROM paper_positions
        WHERE status = 'closed'
          AND trade_nature IN ({in_clause})
          AND closed_at >= NOW() - (:days || ' days')::interval
          {acct_clause}
        """
    )
    rows = db.execute(sql, params).fetchall()
    returns: List[float] = []
    for side, entry, close in rows:
        try:
            e = float(entry or 0.0)
            c = float(close or 0.0)
            if e <= 0 or c <= 0:
                continue
            sign = 1.0 if str(side).lower() in ("long", "buy") else -1.0
            returns.append(sign * (c - e) / e)
        except (TypeError, ValueError):
            continue

    n = len(returns)
    if n == 0:
        return {"trade_count": 0, "win_rate": None, "avg_return_pct": None,
                "net_expectancy_pct": None, "opens_per_day": 0.0}
    wins = [r for r in returns if r > 0]
    avg_ret = sum(returns) / n
    # 净扣费期望：减去一次往返成本（按该 tier 代表 nature 估算）
    try:
        from backend.services.fee_guard import fee_guard
        _nat = nature_list[0] if nature_list else "swing"
        _cost = fee_guard.estimate_breakeven_move(
            notional_usd=2000.0, is_maker=False,
            trade_nature=_nat if _nat in ("swing", "trend_follow", "position") else "swing",
        )
    except Exception:
        _cost = 0.0021
    return {
        "trade_count": n,
        "win_rate": round(len(wins) / n, 4),
        "avg_return_pct": round(avg_ret, 6),
        "net_expectancy_pct": round(avg_ret - _cost, 6),
        "round_trip_cost_pct": round(_cost, 6),
        "opens_per_day": round(n / max(1, lookback_days), 2),
    }


def _open_positions_by_tier(db, account_id: Optional[int]) -> Dict[str, int]:
    from sqlalchemy import text
    params: Dict[str, Any] = {}
    acct_clause = ""
    if account_id is not None:
        acct_clause = " AND account_id = :acct "
        params["acct"] = account_id
    sql = text(
        f"""
        SELECT trade_nature, COUNT(*)
        FROM paper_positions
        WHERE status = 'open' {acct_clause}
        GROUP BY trade_nature
        """
    )
    counts = {"mid": 0, "long": 0, "short": 0}
    for nature, cnt in db.execute(sql, params).fetchall():
        nl = str(nature or "").lower()
        if nl == "swing":
            counts["mid"] += int(cnt)
        elif nl in ("trend_follow", "position"):
            counts["long"] += int(cnt)
        elif nl in ("scalp", "intraday"):
            counts["short"] += int(cnt)
    return counts


def build_midlong_health(lookback_days: int = 14, account_id: Optional[int] = None) -> Dict[str, Any]:
    report: Dict[str, Any] = {"lookback_days": lookback_days, "account_id": account_id}

    # 若未指定账户，取当前 running 会话的 paper 账户
    resolved_acct = account_id
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import FullAutoSession, PaperBalance
        db = SessionLocal()
        try:
            # [2026-08-14 F5 整改] 带租户上下文：此前无上下文导致 RLS 过滤空表，
            # 周报「paper_* 表无样本」是假的。统一按管理口径读取（与
            # get_fixed_symbols_for_session 等同模式）。
            try:
                db.connection().exec_driver_sql("SET app.is_admin = 'on'")
            except Exception:
                pass
            if resolved_acct is None:
                sess = db.query(FullAutoSession).filter(
                    FullAutoSession.status.in_(("running", "defensive"))
                ).first()
                if sess:
                    resolved_acct = sess.paper_account_id or sess.account_id
            report["account_id"] = resolved_acct

            # 1) 每 tier 成交统计 + 活跃度
            report["tiers"] = {}
            for tier, natures in _TIER_NATURES.items():
                report["tiers"][tier] = _tier_trade_stats(db, lookback_days, natures, resolved_acct)

            # 2) 当前持仓分布
            report["open_positions"] = _open_positions_by_tier(db, resolved_acct)

            # 3) 长线周开单 vs 上限
            try:
                from backend.config.settings import TREND_MAX_OPENS_PER_WEEK
                from backend.services.decision_core.fee_context import count_nature_opens
                wk = count_nature_opens(db, int(resolved_acct), nature="trend_follow", since_days=7) if resolved_acct else 0
                report["long_weekly"] = {
                    "opens_7d": wk,
                    "cap": int(TREND_MAX_OPENS_PER_WEEK),
                    "at_cap": bool(wk >= int(TREND_MAX_OPENS_PER_WEEK)),
                }
            except Exception as e:
                report["long_weekly"] = {"error": str(e)}

            # 4) 预算利用率
            try:
                from backend.services.budget_service import budget_service
                equity = 0.0
                if resolved_acct:
                    bal = db.query(PaperBalance).filter(PaperBalance.account_id == resolved_acct).first()
                    if bal:
                        equity = float(bal.available_balance or 0) + float(bal.frozen_margin or 0)
                report["budget"] = budget_service.get_budget_utilization(equity, mode="paper")
                report["equity"] = round(equity, 2)
            except Exception as e:
                report["budget"] = {"error": str(e)}
        finally:
            db.close()
    except Exception as e:
        report["error"] = str(e)

    # 5) 当前生效的开仓门槛（runtime_tuning by_nature）
    try:
        from backend.services.runtime_tuning_store import get_all_tuning
        rt = get_all_tuning() or {}
        by_nature = rt.get("by_nature") or {}
        report["gates"] = {
            "swing": by_nature.get("swing"),
            "trend_follow": by_nature.get("trend_follow"),
            "global_min_risk_reward": (rt.get("min_risk_reward") or {}).get("value")
            if isinstance(rt.get("min_risk_reward"), dict) else rt.get("min_risk_reward"),
        }
    except Exception as e:
        report["gates"] = {"error": str(e)}

    # 6) 激活开关状态
    try:
        from backend.config.settings import (
            MIDLONG_ACTIVATION_ENABLED, MIDLONG_SCAN_BATCH,
            MIDLONG_ACTIVE_EXIT_ENABLED,
        )
        report["activation"] = {
            "enabled": bool(MIDLONG_ACTIVATION_ENABLED),
            "scan_batch": int(MIDLONG_SCAN_BATCH),
            "active_exit": bool(MIDLONG_ACTIVE_EXIT_ENABLED),
        }
    except Exception:
        pass

    # 7) 信号质量（S3 汇合）：校准器 / EV 闸门 / MTF 否决率 / 各开关
    sq: Dict[str, Any] = {}
    try:
        from backend.services.calibration.confidence_calibrator import (
            swing_calibrator, trend_calibrator,
        )
        sq["calibration"] = {
            "swing": swing_calibrator.get_stats(),
            "trend": trend_calibrator.get_stats(),
        }
    except Exception as e:
        sq["calibration"] = {"error": str(e)}
    try:
        from backend.services.decision_core.midlong_ev_gate import midlong_ev_gate
        sq["ev_gate"] = midlong_ev_gate.get_stats()
    except Exception as e:
        sq["ev_gate"] = {"error": str(e)}
    try:
        from backend.services.decision_core.midlong_mtf_constraint import get_mtf_stats
        sq["mtf_constraint"] = get_mtf_stats()
    except Exception as e:
        sq["mtf_constraint"] = {"error": str(e)}
    try:
        from backend.config.settings import (
            MIDLONG_CALIBRATOR_ENABLED, MIDLONG_EV_GATE_ENABLED,
            MIDLONG_MTF_ENFORCE_ENABLED, MIDLONG_QUANT_BRIEF_IN_PROMPT,
            MIDLONG_PAPER_PROBE_STRICT, MIDLONG_FACTOR_RESEARCH_ENABLED,
        )
        sq["flags"] = {
            "calibrator": bool(MIDLONG_CALIBRATOR_ENABLED),
            "ev_gate": bool(MIDLONG_EV_GATE_ENABLED),
            "mtf_enforce": bool(MIDLONG_MTF_ENFORCE_ENABLED),
            "quant_brief": bool(MIDLONG_QUANT_BRIEF_IN_PROMPT),
            "paper_probe_strict": bool(MIDLONG_PAPER_PROBE_STRICT),
            "factor_research": bool(MIDLONG_FACTOR_RESEARCH_ENABLED),
        }
    except Exception:
        pass
    report["signal_quality"] = sq

    # 8) 中长线活跃因子集健康（因子 IC / 数量 / Top）
    try:
        from backend.services.factor_engine.midlong_active_factor_set import (
            midlong_active_factor_set,
        )
        report["factor_set"] = midlong_active_factor_set.get_health_snapshot()
    except Exception as e:
        report["factor_set"] = {"error": str(e)}

    return report
