# -*- coding: utf-8 -*-
"""[F61] 交易中心聚合 API —— `/api/trading/*`（设计文档 §6 收敛目标）。

为什么需要这一层：
  前端「套利中心」要回答四个问题——哪条车道赚钱、赚多少、成本构成、能不能执行。
  这些答案散落在 `arbitrage` / `rebate` / `arbitrage-paper` 三个命名空间里，
  且**成本口径不一致**（有的用旧费率表，有的没扣费）。本文件把它们收敛成
  一个命名空间，并且**所有机会一律给「扣费后净边际」**，为负时 `executable=False`。

事实源：
  - 车道/晋升：`lane_registry`
  - 成交/归因/持仓：`lane_ledger`（六维账本）
  - 费率：`fee_schedule_service`（引擎唯一权威；Aster maker 0% / taker 0.04%）
  - 资金费：`perp_funding`（毫秒时间戳）

旧路径保持不变（6 个月兼容），前端逐步切到本命名空间。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trading", tags=["trading-hub"])

# 参考权益：车道预算按百分比分配，组合权益取模拟账户实际权益优先，
# 取不到时用这个基准（前端会显示 equity_source，不隐藏假设）。
DEFAULT_REFERENCE_EQUITY = 5000.0

# 资金费机会的成本口径：一个周期（8h）内建仓+平仓的 taker 往返
CARRY_ROUND_TRIP_TAKER_BP = 8.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lanes() -> List[Dict[str, Any]]:
    from backend.services import lane_registry as reg

    return reg.list_lanes()


def _lane_budget_equity(lanes: List[Dict[str, Any]], reference: float) -> Dict[str, float]:
    """按 budget_pct 把参考权益分配到各车道。"""
    out: Dict[str, float] = {}
    for ln in lanes:
        pct = float(((ln.get("risk") or {}).get("budget_pct")) or 0.0)
        out[ln["lane_id"]] = round(reference * pct / 100.0, 2)
    return out


def _resolve_equity(lane_id: Optional[str] = None) -> tuple:
    """权益口径：指定车道 → 该车道自己的模拟账户；否则全部模拟账户合计。

    设计 §3.2：每条车道一个独立模拟账户、独立权益、独立风控。取不到时回落到
    参考值，并通过 `equity_source` 明示（不隐藏假设）。
    """
    try:
        from sqlalchemy import text

        from backend.core.tenant import system_identity
        from backend.database.connection import SessionLocal

        acct_id = None
        if lane_id:
            from backend.services import lane_registry as reg

            lane = reg.get_lane(lane_id) or {}
            acct_id = (lane.get("meta") or {}).get("paper_account_id")

        with system_identity():
            with SessionLocal() as db:
                if acct_id:
                    row = db.execute(text(
                        "SELECT total_equity AS eq, 1 AS n FROM arbitrage_paper_accounts"
                        " WHERE id = :i AND status <> 'deleted'"
                    ), {"i": int(acct_id)}).mappings().first()
                    if row and row["eq"] and float(row["eq"]) > 0:
                        return float(row["eq"]), f"lane_account({acct_id})"
                row = db.execute(text(
                    "SELECT SUM(total_equity) AS eq, COUNT(*) AS n"
                    " FROM arbitrage_paper_accounts WHERE status <> 'deleted'"
                )).mappings().first()
        if row and row["eq"] and float(row["eq"]) > 0:
            return float(row["eq"]), f"paper_accounts({int(row['n'] or 0)})"
    except Exception as e:
        logger.debug("[TradingHub] 读取模拟账户权益失败: %s", e)
    return DEFAULT_REFERENCE_EQUITY, "default_reference"


# ═══════════════════════ ① 组合总览 ═══════════════════════

@router.get("/account/unified")
def account_unified(account_id: Optional[int] = None,
                    days: float = 30.0) -> Dict[str, Any]:
    """**统一模拟账户视图**：一个账户、多条策略、按策略分账。

    用户决策（2026-09-09）：影子期不是独立账户，而是统一账户里的一条策略配置
    （`strategy_type=MM`），资金/风控/盈亏与 S3/S8/SDN 同账管理，账户层面看到整体交易，
    各策略之间可以相互配合（共享额度、共享风控上限）。

    返回：
      - `account`：权益、可用、按交易所额度与策略配额
      - `strategies`：按 `strategy_type` 聚合的盈亏/手续费/成交笔数
      - `positions`：账户内各策略的持仓（MM 来自 `lane_ledger`，其它来自 rebate 监控）
      - `exposure`：账户级敞口与上限使用率
    """
    from sqlalchemy import text

    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    from backend.services import lane_ledger, lane_registry

    # 选账户：显式指定 > 车道绑定 > 第一个未删除
    acct_id = account_id
    if acct_id is None:
        for ln in lane_registry.list_lanes():
            pid = (ln.get("meta") or {}).get("paper_account_id")
            if pid:
                acct_id = int(pid)
                break
    with system_identity():
        with SessionLocal() as db:
            if acct_id is None:
                row = db.execute(text(
                    "SELECT id FROM arbitrage_paper_accounts WHERE status <> 'deleted'"
                    " ORDER BY id LIMIT 1"
                )).mappings().first()
                acct_id = int(row["id"]) if row else None
            if acct_id is None:
                raise HTTPException(status_code=404, detail="无可用模拟账户")
            acct = db.execute(text(
                "SELECT id, name, total_equity, available_balance, frozen_balance,"
                " realized_pnl, status, allocation_preset, metadata_json"
                " FROM arbitrage_paper_accounts WHERE id = :i"
            ), {"i": acct_id}).mappings().first()
            if not acct:
                raise HTTPException(status_code=404, detail=f"账户不存在: {acct_id}")
            bals = db.execute(text(
                "SELECT exchange, allocated_usd, available_usd, frozen_usd,"
                " strategy_limits_json FROM arbitrage_paper_exchange_balances"
                " WHERE account_id = :i ORDER BY exchange"
            ), {"i": acct_id}).mappings().all()
            ledger = db.execute(text(
                "SELECT COALESCE(strategy_type, '(未标注)') AS strategy,"
                " action, COUNT(*) AS n, SUM(amount_usd) AS amount"
                " FROM arbitrage_paper_ledgers WHERE account_id = :i"
                " AND created_at >= now() - make_interval(secs => :secs)"
                " GROUP BY strategy, action ORDER BY strategy, action"
            ), {"i": acct_id, "secs": float(days) * 86400.0}).mappings().all()

    # 按策略聚合账本
    strat: Dict[str, Dict[str, Any]] = {}
    for r in ledger:
        s = strat.setdefault(r["strategy"], {
            "strategy_type": r["strategy"], "pnl_usd": 0.0, "fee_usd": 0.0,
            "rebate_usd": 0.0, "slippage_usd": 0.0, "capital_usd": 0.0,
            "entries": 0, "fills": 0,
        })
        amt = float(r["amount"] or 0.0)
        n = int(r["n"] or 0)
        s["entries"] += n
        if r["action"] == "paper_pnl":
            s["pnl_usd"] += amt
            s["fills"] = s.get("fills", 0) + n
        elif r["action"] == "paper_fee":
            s["fee_usd"] += amt
        elif r["action"] == "paper_rebate":
            s["rebate_usd"] += amt
        elif r["action"] == "paper_slippage":
            s["slippage_usd"] += amt
        elif r["action"] == "strategy_capital_alloc":
            s["capital_usd"] += amt
        elif r["action"] == "paper_fill":
            s["fills"] = s.get("fills", 0) + n
    for s in strat.values():
        s["net_usd"] = round(s["pnl_usd"] + s["fee_usd"] + s["rebate_usd"]
                             + s["slippage_usd"], 4)
        for k in ("pnl_usd", "fee_usd", "rebate_usd", "slippage_usd", "capital_usd"):
            s[k] = round(s[k], 4)

    # 持仓：MM 来自车道账本；其它策略来自 rebate 监控
    mm_positions: List[Dict[str, Any]] = []
    try:
        marks = _latest_marks()
        mm_positions = [p for p in lane_ledger.open_positions(days=days, marks=marks)
                        if abs(p["qty"]) > 1e-12]
    except Exception as e:
        logger.debug("[TradingHub] MM 持仓读取失败: %s", e)

    exchange_rows = []
    for b in bals:
        limits = b["strategy_limits_json"]
        if isinstance(limits, str):
            try:
                limits = json.loads(limits)
            except Exception:
                limits = {}
        exchange_rows.append({
            "exchange": b["exchange"],
            "allocated_usd": round(float(b["allocated_usd"] or 0.0), 2),
            "available_usd": round(float(b["available_usd"] or 0.0), 2),
            "frozen_usd": round(float(b["frozen_usd"] or 0.0), 2),
            "strategy_limits": limits or {},
            "strategy_budgets": {k: round(float(b["allocated_usd"] or 0.0) * float(v), 2)
                                 for k, v in (limits or {}).items()},
        })

    equity = float(acct["total_equity"] or 0.0)
    mm_exposure = sum(abs(p["notional_usd"]) for p in mm_positions)
    return {
        "account": {
            "account_id": int(acct["id"]), "name": acct["name"],
            "total_equity": round(equity, 2),
            "available_balance": round(float(acct["available_balance"] or 0.0), 2),
            "frozen_balance": round(float(acct["frozen_balance"] or 0.0), 2),
            "realized_pnl": round(float(acct["realized_pnl"] or 0.0), 2),
            "status": acct["status"], "preset": acct["allocation_preset"],
        },
        "exchanges": exchange_rows,
        "strategies": sorted(strat.values(), key=lambda x: -abs(x["net_usd"])),
        "positions": {
            "mm": mm_positions,
            "count": len(mm_positions),
        },
        "exposure": {
            "mm_notional_usd": round(mm_exposure, 2),
            "mm_notional_pct": round(mm_exposure / equity * 100.0, 3) if equity else 0.0,
        },
        "window_days": days,
        "as_of": _now_iso(),
    }


@router.get("/portfolio/summary")
def portfolio_summary() -> Dict[str, Any]:
    """组合权益 / 今日盈亏 / 风险预算 / 熔断计数（总览页数据源）。"""
    from backend.services import lane_ledger

    equity, src = _resolve_equity()
    lanes = _lanes()
    attr_1d = lane_ledger.attribution(days=1.0)
    attr_7d = lane_ledger.attribution(days=7.0)
    total_1d = attr_1d.get("total") or {}
    total_7d = attr_7d.get("total") or {}

    budgets = _lane_budget_equity(lanes, equity)
    active = [ln for ln in lanes if (ln.get("status") or "") == "active"]
    # 风险预算占用 = 已启用车道的预算之和（设计文档总览页「风险预算用 38%」）
    used_pct = sum(float(((ln.get("risk") or {}).get("budget_pct")) or 0.0) for ln in active)

    breakers = _breaker_rows(lanes)
    return {
        "equity": round(equity, 2),
        "equity_source": src,
        "pnl_today_usd": round(float(total_1d.get("net_usd") or 0.0), 4),
        "pnl_today_pct": round(float(total_1d.get("net_usd") or 0.0) / equity * 100.0, 4)
        if equity else 0.0,
        "pnl_7d_usd": round(float(total_7d.get("net_usd") or 0.0), 4),
        "pnl_7d_pct": round(float(total_7d.get("net_usd") or 0.0) / equity * 100.0, 4)
        if equity else 0.0,
        "fills_today": int(total_1d.get("n") or 0),
        "notional_today": round(float(total_1d.get("notional") or 0.0), 2),
        "budget_used_usd": round(equity * used_pct / 100.0, 2),
        "budget_used_pct": round(used_pct, 2),
        "lanes_active": len(active),
        "lanes_total": len(lanes),
        "breakers_active": sum(1 for b in breakers if b.get("state") == "tripped"),
        "lane_budgets": budgets,
        "as_of": _now_iso(),
    }


# ═══════════════════════ ② 持仓 ═══════════════════════

@router.get("/positions")
def positions(lane_id: Optional[str] = None, symbol: Optional[str] = None,
              days: float = 30.0) -> Dict[str, Any]:
    """跨车道统一持仓表（从六维账本重建，不依赖各车道自己的持仓表）。"""
    from backend.services import lane_ledger

    marks = _latest_marks()
    items = lane_ledger.open_positions(lane_id=lane_id, days=days, marks=marks)
    if symbol:
        items = [x for x in items if x["symbol"].upper() == symbol.upper()]
    open_items = [x for x in items if abs(x["qty"]) > 1e-12]
    return {
        "items": items,
        "count": len(items),
        "open_count": len(open_items),
        "net_exposure_usd": round(sum(x["notional_usd"] * (1 if x["qty"] > 0 else -1)
                                      for x in open_items), 2),
        "gross_exposure_usd": round(sum(x["notional_usd"] for x in open_items), 2),
        "unrealized_usd": round(sum(x["unrealized_usd"] for x in open_items), 4),
        "as_of": _now_iso(),
    }


def _latest_marks(symbols: Optional[List[str]] = None) -> Dict[str, float]:
    """最新中间价（用于未平仓浮动盈亏）。"""
    try:
        from sqlalchemy import text

        from backend.core.tenant import system_identity
        from backend.database.connection import MarketSessionLocal

        with system_identity():
            with MarketSessionLocal() as db:
                rows = db.execute(text(
                    "SELECT DISTINCT ON (symbol) symbol,"
                    " (best_bid+best_ask)/2.0 AS mid"
                    " FROM market_orderbook_snapshots"
                    " WHERE best_bid>0 AND best_ask>best_bid"
                    " ORDER BY symbol, timestamp DESC"
                )).mappings().all()
        out = {r["symbol"]: float(r["mid"]) for r in rows}
        if symbols:
            return {k: v for k, v in out.items() if k in symbols}
        return out
    except Exception as e:
        logger.debug("[TradingHub] 取中间价失败: %s", e)
        return {}


# ═══════════════════════ ③ 机会 ═══════════════════════

@router.get("/opportunities")
def opportunities(venue: str = "asterdex", limit: int = 40,
                  days: float = 30.0, min_days: float = 7.0,
                  tradable_only: bool = True) -> Dict[str, Any]:
    """机会表：**一律给扣费后净边际**，为负时不可执行（成本纪律）。

    两类机会：
      - 做市（maker）：毛边际 = 双边挂宽 2×w；成本 = maker 往返（Aster 0%）；
        另需扣除实测逆选择（取车道 edge 的 price_bp，未验证时按 0 并在 note 标注）。
      - 资金费 carry：毛边际 = 持有 `days` 天的累计资金费（按场地结算周期折算）；
        成本 = 永续 + 现货两条腿的 taker 往返；另给盈亏平衡持有天数。

    **数据质量过滤**（否则新上线标的的极端资金费会霸榜）：
      - `min_days`：该标的必须有 ≥ N 天的费率样本（上市首日尖峰不算机会）；
      - `tradable_only`：必须在 `market_orderbook_snapshots` 里有盘口（我们真能交易）；
      - 年化 >100% 或盈亏平衡 <2 天 → 标记 `suspect=true` 并排到末尾。
    """
    items: List[Dict[str, Any]] = []
    items.extend(_maker_opportunities(venue))
    items.extend(_carry_opportunities(venue, days=max(7.0, float(days)),
                                      min_days=float(min_days),
                                      tradable_only=bool(tradable_only)))
    items.sort(key=lambda x: (bool(x.get("suspect")), not x["executable"], -float(x["net_bp"])))
    return {
        "venue": venue,
        "items": items[:max(1, int(limit))],
        "count": len(items),
        "executable_count": sum(1 for x in items if x["executable"]),
        "suspect_count": sum(1 for x in items if x.get("suspect")),
        "filters": {"min_days": min_days, "tradable_only": tradable_only},
        "as_of": _now_iso(),
    }


def _maker_opportunities(venue: str) -> List[Dict[str, Any]]:
    from backend.services import lane_registry as reg
    from backend.services.market_maker.core import QuoteParams

    lane = reg.get_lane("mm_asterdex") or {}
    edge = lane.get("edge") or {}
    params = QuoteParams()
    gross = 2.0 * float(params.w_base_bp)                    # 双边各挂 w
    adverse = abs(min(0.0, float(edge.get("price_bp") or 0.0)))
    try:
        from backend.services import fee_schedule_service as fs

        rules = fs.get_exchange_rules(venue) or {}
        maker_bp = float(rules.get("maker_fee_rate") or 0.0) * 1e4
    except Exception:
        maker_bp = 0.0
    cost = 2.0 * maker_bp + adverse
    theoretical = gross - cost
    # 有实测净期望时以实测为准（理论值只作展示）——避免把毛边际当成可赚的钱
    measured = edge.get("net_bp")
    net = float(measured) if measured is not None else theoretical
    verified = measured is not None
    symbols = ((lane.get("meta") or {}).get("symbols")
               or ["BTC", "ETH", "BNB", "XRP", "SOL", "DOGE"])
    equity, _src = _resolve_equity("mm_asterdex")
    budget_pct = float(((lane.get("risk") or {}).get("budget_pct")) or 0.0)
    per_symbol_cap = equity * budget_pct / 100.0 / max(1, len(symbols))
    out: List[Dict[str, Any]] = []
    for s in symbols:
        out.append({
            "kind": "market_making", "symbol": s, "venue": venue,
            "gross_bp": round(gross, 3), "cost_bp": round(cost, 3),
            "net_bp": round(net, 3), "theoretical_net_bp": round(theoretical, 3),
            "capacity_usd": round(per_symbol_cap, 2),
            "confidence": "measured" if verified else "unverified",
            "executable": bool(net > 0 and verified),
            "reason": "" if (net > 0 and verified) else (
                "净期望未验证（缺影子期/回放实测）" if not verified
                else "扣费后净边际为负"),
            "note": (f"实测净期望 {float(measured):+.2f}bp；理论双边挂宽 {gross:.1f}bp "
                     f"− 成本 {cost:.2f}bp") if verified
            else f"w={params.w_base_bp}bp 双边；逆选择未验证（按 0 计）",
        })
    return out


def _carry_opportunities(venue: str, days: float = 30.0, min_days: float = 7.0,
                         tradable_only: bool = True) -> List[Dict[str, Any]]:
    """资金费机会：真实 `perp_funding` 分布 vs 一次性进出成本。

    为什么不是「拿最新一条费率算一笔」：
      资金费按周期结算，单期只有 0.1–3bp，而进出要付两条腿的 taker 成本。
      所以必须用**持有 N 天**的口径算净收益，并给出「盈亏平衡持有天数」。
    现货腿状态：库里没有现货行情、也没有现货下单通道（设计文档 §1.3 已列为待建），
      因此即使净收益为正也标记 `executable=false`，reason 说明缺哪条腿。
    """
    from backend.services.carry import funding_model as fm

    try:
        from sqlalchemy import text

        from backend.core.tenant import system_identity
        from backend.database.connection import MarketSessionLocal

        cutoff_ms = int((__import__("time").time() - float(days) * 86400.0) * 1000)
        with system_identity():
            with MarketSessionLocal() as db:
                # 在 SQL 里聚合：`perp_funding` 907 万行，逐行拉取会超时
                rows = db.execute(text(
                    "SELECT symbol, COUNT(*) AS n,"
                    " COUNT(DISTINCT (timestamp / 86400000)) AS days_n,"
                    " AVG(funding_rate)*1e4 AS mean_bp,"
                    " STDDEV_SAMP(funding_rate)*1e4 AS std_bp,"
                    " AVG(CASE WHEN funding_rate>0 THEN 1.0 ELSE 0.0 END) AS pos_ratio"
                    " FROM perp_funding WHERE exchange=:e AND timestamp >= :ts"
                    " GROUP BY symbol HAVING COUNT(*) >= 30"
                ), {"e": venue, "ts": cutoff_ms}).mappings().all()
                tradable = set()
                if tradable_only:
                    tradable = {str(r["symbol"]) for r in db.execute(text(
                        "SELECT DISTINCT symbol FROM market_orderbook_snapshots"
                        " WHERE exchange=:e AND timestamp >= :ts"
                    ), {"e": venue, "ts": cutoff_ms}).mappings().all()}
    except Exception as e:
        logger.debug("[TradingHub] 资金费查询失败: %s", e)
        return []

    cost = fm.DEFAULT_PERP_ROUND_TRIP_BP + fm.DEFAULT_SPOT_ROUND_TRIP_BP
    ppd = fm.periods_per_day(venue)
    equity, _src = _resolve_equity("carry_basis")
    # 对冲后实测统计（F70 回测写入 carry_basis 车道 meta）：有就用实测，没有就退回资金费估算
    hedged: Dict[str, Any] = {}
    hedged_hold = None
    try:
        from backend.services import lane_registry as reg

        meta = (reg.get_lane("carry_basis") or {}).get("meta") or {}
        hs = meta.get("hedged_stats") or {}
        hedged = hs.get("per_symbol") or {}
        hedged_hold = hs.get("hold_days")
    except Exception:
        hedged, hedged_hold = {}, None

    candidates: List[Dict[str, Any]] = []
    for r in rows:
        sym = str(r["symbol"])
        n = int(r["n"] or 0)
        days_n = int(r["days_n"] or 0)
        if days_n < min_days:
            continue
        if tradable_only and sym not in tradable:
            continue
        mean_bp = float(r["mean_bp"] or 0.0)
        std_bp = float(r["std_bp"] or 0.0)
        pos_ratio = float(r["pos_ratio"] or 0.0)
        decision = fm.decide_carry(avg_funding_bp=mean_bp, cost_bp=cost,
                                   planned_hold_days=30.0, venue=venue)
        t = (mean_bp / (std_bp / (n ** 0.5))) if std_bp > 0 and n > 1 else 0.0
        ann = float(decision.get("annualized_pct") or 0.0)
        be = decision.get("breakeven_days")
        # 现货基差（有现货数据时才算，否则 None——不假装知道）
        basis = None
        try:
            from backend.services.carry import spot_collector as sc

            # 永续价用**有新鲜盘口的场地**：carry 的永续腿本来就可能在不同所
            perp_venue = venue if sc.latest_perp_mark(sym, venue) else "binance"
            b = sc.basis_bp(sym, perp_venue)
            basis = None if not b else b["basis_bp"]
        except Exception:
            basis = None

        h = hedged.get(sym) or {}
        hedged_net = h.get("net_bp")
        # 有实测对冲结果时，net_bp 用**实测**（含两条腿成本与基差收敛），
        # 不再用「资金费 − 成本」的估算；估算值保留在 theoretical_net_bp。
        net = float(hedged_net) if hedged_net is not None else decision["net_bp"]
        verified = hedged_net is not None
        suspect = bool(ann > 100.0 or (be is not None and be < 2.0) or pos_ratio < 0.6)
        if verified:
            suspect = suspect or float(h.get("t") or 0) < 1.0
        candidates.append({
            "kind": "funding_carry", "symbol": sym, "venue": venue,
            "gross_bp": round(mean_bp * ppd * 30.0, 4),
            "cost_bp": round(cost, 3),
            "net_bp": round(net, 4),
            "theoretical_net_bp": decision["net_bp"],
            "capacity_usd": round(equity * 0.25 / max(1, len(rows)), 2),
            "confidence": "measured" if verified else "estimated",
            # 现货腿：模拟盘有纸面现货通道（SpotPaperChannel），实盘未接
            "executable": bool(verified and net > 0),
            "suspect": suspect,
            "reason": ("" if (verified and net > 0) else
                       ("对冲后净期望为负（实测）" if verified and net <= 0
                        else "尚无对冲后实测（仅资金费估算）")),
            "note": ((f"对冲实测：{h.get('n')} 笔、持有 {hedged_hold} 天、"
                      f"净 {float(hedged_net):+.2f}bp、t={h.get('t')}、"
                      f"胜率 {(float(h.get('win_ratio') or 0))*100:.0f}%；"
                      f"资金费 +${h.get('funding_usd')}、基差收敛 "
                      f"{h.get('basis_convergence_bp')}bp")
                     if verified else
                     f"仅资金费估算：均值 {mean_bp:+.4f}bp/{fm.period_hours(venue):.0f}h"
                     f"（正比例 {pos_ratio*100:.1f}%，样本 {n}，t={t:.1f}）；"
                     f"盈亏平衡 {be} 天"),
            "direction": "short_perp_long_spot" if mean_bp > 0
            else "long_perp_spot_reverse",
            "breakeven_days": be,
            "required_hold_days": hedged_hold,
            "annualized_pct": ann,
            "funding_mean_bp": round(mean_bp, 5),
            "funding_positive_ratio": round(pos_ratio, 4),
            "funding_t": round(t, 3),
            "funding_samples": n,
            "funding_days": days_n,
            "period_hours": fm.period_hours(venue),
            "basis_bp": None if basis is None else round(basis, 4),
            "hedged_net_bp": None if hedged_net is None else round(float(hedged_net), 4),
            "hedged_samples": h.get("n"),
            "spot_leg_available": True,       # 模拟盘纸面现货通道已具备
            "execution_mode": "paper_spot_channel",
        })
    return candidates


# ═══════════════════════ ④ 风险与资金 ═══════════════════════

BREAKER_KINDS = ("data", "fee", "daily_loss", "toxic_flow", "drill")


def _classify_breaker(reason: str) -> str:
    """把 health.breaker 的自由文本归类到熔断类型。

    之前所有非空 breaker 都落到 `fee` 行，导致「数据断流」显示在费率格子里
    （前端如实报了这个语义错位）。这里按原因前缀归类。
    """
    s = str(reason or "").lower()
    if not s:
        return ""
    if s.startswith("stale_data") or "no_snapshot" in s or "no_market" in s:
        return "data"
    if "maker_fee" in s or "fee" in s:
        return "fee"
    if s.startswith("drill"):
        return "drill"
    if "toxic" in s:
        return "toxic_flow"
    if "daily_loss" in s or "loss_stop" in s:
        return "daily_loss"
    return ""


def _breaker_rows(lanes: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """熔断矩阵：车道 × 熔断类型（数据/费率/日亏/毒性流/演练）。

    每格给出 `state`（ok/tripped）、`ts`、`reason`。`health.breaker` 按原因归类，
    不再一律塞进 fee 行；`drill` 是独立的演练类型（前端按 state 上色）。
    """
    lanes = lanes if lanes is not None else _lanes()
    out: List[Dict[str, Any]] = []
    for ln in lanes:
        health = ln.get("health") or {}
        lane_id = ln["lane_id"]
        risk = ln.get("risk") or {}
        breaker_txt = str(health.get("breaker") or "")
        kind = _classify_breaker(breaker_txt)
        age = health.get("data_age_sec")
        updated = health.get("updated_at")

        data_tripped = (age is not None and age > 300.0) or kind == "data"
        out.append({
            "lane_id": lane_id, "breaker": "data",
            "state": "tripped" if data_tripped else "ok",
            "ts": updated,
            "reason": breaker_txt if kind == "data" else (health.get("note") or ""),
            "data_age_sec": age,
        })
        out.append({
            "lane_id": lane_id, "breaker": "fee",
            "state": "tripped" if kind == "fee" else "ok",
            "ts": updated, "reason": breaker_txt if kind == "fee" else "",
        })
        out.append({
            "lane_id": lane_id, "breaker": "daily_loss",
            "state": "tripped" if bool(risk.get("daily_loss_tripped")) else "ok",
            "ts": risk.get("daily_loss_ts"), "reason": risk.get("daily_loss_reason") or "",
        })
        out.append({
            "lane_id": lane_id, "breaker": "toxic_flow",
            "state": "tripped" if kind == "toxic_flow" else "ok",
            "ts": updated, "reason": breaker_txt if kind == "toxic_flow" else "",
        })
        # 演练（drill）：真的停报价；前端按 state 上色并可一键解除
        out.append({
            "lane_id": lane_id, "breaker": "drill",
            "state": "tripped" if bool(health.get("drill")) else "ok",
            "ts": updated,
            "reason": (health.get("drill_reason") or "演练中") if health.get("drill") else "",
        })
    return out


@router.get("/risk/summary")
def risk_summary(days: float = 30.0) -> Dict[str, Any]:
    """风险速览：最大单币敞口 / 最差单日 / 熔断历史。"""
    from backend.services import lane_ledger

    equity, src = _resolve_equity()
    pos = positions(days=days)
    open_items = [x for x in pos["items"] if abs(x["qty"]) > 1e-12]
    by_symbol: Dict[str, float] = {}
    for x in open_items:
        by_symbol[x["symbol"]] = by_symbol.get(x["symbol"], 0.0) + x["notional_usd"]
    max_sym = max(by_symbol.items(), key=lambda kv: kv[1]) if by_symbol else (None, 0.0)
    series = lane_ledger.daily_series(days=days)
    worst = min(series, key=lambda r: r["net_usd"]) if series else None
    lanes = _lanes()
    try:
        from backend.services import lane_registry as reg

        history = reg.breaker_history(days=days)
    except Exception:
        history = []
    return {
        "equity": round(equity, 2), "equity_source": src,
        "max_symbol": max_sym[0],
        "max_symbol_exposure_usd": round(max_sym[1], 2),
        "max_symbol_exposure_pct": round(max_sym[1] / equity * 100.0, 2) if equity else 0.0,
        "net_exposure_usd": pos["net_exposure_usd"],
        "gross_exposure_usd": pos["gross_exposure_usd"],
        "net_exposure_pct": round(abs(pos["net_exposure_usd"]) / equity * 100.0, 2)
        if equity else 0.0,
        "unrealized_usd": pos["unrealized_usd"],
        "worst_day": (worst or {}).get("date"),
        "worst_day_usd": round(float((worst or {}).get("net_usd") or 0.0), 4),
        "daily_series": series,
        "breaker_history": history,
        "breaker_history_total": sum(h["trips"] for h in history),
        "limits": {
            "max_net_exposure_pct": max(
                [float(((ln.get("risk") or {}).get("max_net_exposure_pct")) or 0.0)
                 for ln in lanes] or [0.0]),
            "max_symbol_exposure_pct": max(
                [float(((ln.get("risk") or {}).get("max_symbol_exposure_pct")) or 0.0)
                 for ln in lanes] or [0.0]),
            "daily_loss_stop_pct": max(
                [float(((ln.get("risk") or {}).get("daily_loss_stop_pct")) or 0.0)
                 for ln in lanes] or [0.0]),
        },
        "as_of": _now_iso(),
    }


@router.get("/risk/breakers")
def risk_breakers(days: float = 30.0) -> Dict[str, Any]:
    rows = _breaker_rows()
    # 把当前 tripped 状态落进历史表（幂等去重），并回读近 N 天计数
    try:
        from backend.services import lane_registry as reg

        reg.log_breaker(rows)
        history = reg.breaker_history(days=days)
    except Exception as e:
        logger.debug("[TradingHub] 熔断历史读写失败: %s", e)
        history = []
    return {
        "items": rows,
        "tripped": [r for r in rows if r["state"] == "tripped"],
        "history": history,
        "history_total_30d": sum(h["trips"] for h in history),
        "as_of": _now_iso(),
    }


class BreakerResetBody(BaseModel):
    lane_id: str
    breaker: str


@router.post("/risk/breakers/reset")
def reset_breaker(body: BreakerResetBody) -> Dict[str, Any]:
    """手动复位熔断（前端需二次确认）。"""
    from backend.services import lane_registry as reg

    if not reg.get_lane(body.lane_id):
        raise HTTPException(status_code=404, detail=f"车道不存在: {body.lane_id}")
    try:
        reg.update_health(body.lane_id, {
            "data_age_sec": 0.0, "breaker": None,
            "note": f"熔断 {body.breaker} 已手动复位 @ {_now_iso()}",
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"复位失败: {e}") from e
    return {"ok": True, "lane_id": body.lane_id, "breaker": body.breaker,
            "as_of": _now_iso()}


# ═══════════════════════ ⑤ 配置 ═══════════════════════

@router.get("/config/datasources")
def config_datasources() -> Dict[str, Any]:
    """数据源健康：盘口/成交/资金费/现货各自的**数据年龄**。

    前端配置页「数据源健康」的数据源。年龄过大即视为断流——
    实测发现 Asterdex 盘口与成交流已停更 22 天，必须能一眼看见。
    """
    import time as _t

    out: List[Dict[str, Any]] = []
    try:
        from sqlalchemy import text

        from backend.core.tenant import system_identity
        from backend.database.connection import MarketSessionLocal

        now_ms = int(_t.time() * 1000)
        with system_identity():
            with MarketSessionLocal() as db:
                for label, sql in (
                    ("orderbook", "SELECT exchange, MAX(timestamp) mx, COUNT(*) n"
                                  " FROM market_orderbook_snapshots GROUP BY exchange"),
                    ("trades", "SELECT exchange, MAX(timestamp) mx, COUNT(*) n"
                               " FROM market_trades_aggregated GROUP BY exchange"),
                    ("funding", "SELECT exchange, MAX(timestamp) mx, COUNT(*) n"
                                " FROM perp_funding GROUP BY exchange"),
                    ("spot", "SELECT exchange, MAX(ts_ms) mx, COUNT(*) n"
                             " FROM market_spot_klines GROUP BY exchange"),
                ):
                    try:
                        rows = db.execute(text(sql)).mappings().all()
                    except Exception:
                        out.append({"source": label, "exchange": None, "rows": 0,
                                    "last_ts": None, "age_sec": None,
                                    "stale": True, "note": "表不存在或查询失败"})
                        continue
                    if not rows:
                        out.append({"source": label, "exchange": None, "rows": 0,
                                    "last_ts": None, "age_sec": None,
                                    "stale": True, "note": "无数据"})
                        continue
                    for r in rows:
                        age = (now_ms - int(r["mx"])) / 1000.0
                        out.append({
                            "source": label, "exchange": r["exchange"],
                            "rows": int(r["n"] or 0), "last_ts": int(r["mx"]),
                            "age_sec": round(age, 1),
                            "stale": age > 600.0,
                        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"数据源健康查询失败: {e}") from e
    return {"items": out, "count": len(out),
            "stale_count": sum(1 for x in out if x["stale"]), "as_of": _now_iso()}


@router.get("/capital/pool")
def capital_pool() -> Dict[str, Any]:
    """资金池：各模拟账户余额（设计 §3.5「资金池」）。"""
    try:
        from sqlalchemy import text

        from backend.core.tenant import system_identity
        from backend.database.connection import SessionLocal

        with system_identity():
            with SessionLocal() as db:
                rows = db.execute(text(
                    "SELECT id, name, total_equity, available_balance, frozen_balance,"
                    " status, allocation_preset FROM arbitrage_paper_accounts"
                    " WHERE status <> 'deleted' ORDER BY id"
                )).mappings().all()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"资金池查询失败: {e}") from e
    items = [{
        "account_id": r["id"], "name": r["name"],
        "total_equity": round(float(r["total_equity"] or 0.0), 2),
        "available_balance": round(float(r["available_balance"] or 0.0), 2),
        "frozen_balance": round(float(r["frozen_balance"] or 0.0), 2),
        "status": r["status"], "preset": r["allocation_preset"],
    } for r in rows]
    return {"items": items, "count": len(items),
            "total_equity": round(sum(i["total_equity"] for i in items), 2),
            "as_of": _now_iso()}


class DrillBody(BaseModel):
    enable: bool
    lanes: Optional[List[str]] = None
    reason: str = "手动演练"


@router.post("/risk/drill")
def risk_drill(body: DrillBody) -> Dict[str, Any]:
    """组合级熔断演练：**真的让车道停止报价**（不是假按钮）。

    做法：在车道 health 里写 `drill=true`，影子期调度器读到即跳过该车道；
    解除后恢复正常。仅对 mode=paper 的车道生效——实盘不允许演练。
    """
    from backend.services import lane_registry as reg

    lanes = body.lanes or [ln["lane_id"] for ln in reg.list_lanes()]
    affected, skipped = [], []
    for lid in lanes:
        lane = reg.get_lane(lid)
        if not lane:
            skipped.append({"lane_id": lid, "reason": "车道不存在"})
            continue
        if lane.get("mode") != "paper":
            skipped.append({"lane_id": lid, "reason": f"mode={lane.get('mode')} 非 paper"})
            continue
        health = dict(lane.get("health") or {})
        health["drill"] = bool(body.enable)
        health["drill_reason"] = body.reason if body.enable else None
        health["breaker"] = ("drill" if body.enable else health.get("breaker"))
        reg.update_health(lid, health)
        affected.append(lid)
    return {"ok": True, "enabled": bool(body.enable), "affected": affected,
            "skipped": skipped, "as_of": _now_iso()}


@router.get("/config/fees")
def config_fees() -> Dict[str, Any]:
    """费率表（引擎权威口径）：Aster 必须显示 maker 0% / taker 0.04%。"""
    from backend.services import fee_schedule_service as fs

    try:
        summary = fs.get_all_exchange_summary() or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"费率读取失败: {e}") from e
    items = []
    for s in summary:
        maker = float(s.get("maker_fee_rate") or 0.0)
        taker = float(s.get("taker_fee_rate") or 0.0)
        items.append({
            "exchange": s.get("exchange"),
            "maker_bp": round(maker * 1e4, 4), "taker_bp": round(taker * 1e4, 4),
            "maker_pct": s.get("maker_fee_pct"), "taker_pct": s.get("taker_fee_pct"),
            "round_trip_maker_bp": round(maker * 2e4, 4),
            "round_trip_taker_bp": round(taker * 2e4, 4),
            "min_notional_usd": s.get("min_notional_usd"),
            "source": "fee_schedule_service",
        })
    return {"items": items, "count": len(items), "as_of": _now_iso()}


LANE_CONFIG_KEYS = ("w_base_bp", "min_width_bp", "min_width_reduce_bp", "max_width_bp",
                    "k_vol", "k_inv", "max_one_side_seconds", "vol_pause_sigma",
                    "max_net_directional_ratio", "max_net_exposure_ratio")


@router.get("/config/lanes/{lane_id}")
def get_lane_config(lane_id: str) -> Dict[str, Any]:
    """车道参数（报价 + 风控）+ 可调键白名单。"""
    from backend.services import lane_registry as reg
    from backend.services.market_maker.core import LaneRiskLimits, QuoteParams

    lane = reg.get_lane(lane_id)
    if not lane:
        raise HTTPException(status_code=404, detail=f"车道不存在: {lane_id}")
    stored = (lane.get("meta") or {}).get("params") or {}
    qp = QuoteParams(**{k: v for k, v in stored.items() if k in QuoteParams.__dataclass_fields__})
    rl = LaneRiskLimits(**{k: v for k, v in stored.items()
                           if k in LaneRiskLimits.__dataclass_fields__})
    return {
        "lane_id": lane_id, "mode": lane.get("mode"),
        "params": {k: getattr(qp, k) for k in QuoteParams.__dataclass_fields__},
        "limits": {k: getattr(rl, k) for k in LaneRiskLimits.__dataclass_fields__},
        "editable_keys": list(LANE_CONFIG_KEYS),
        "source": "lane_registry.meta.params" if stored else "code_defaults",
        "as_of": _now_iso(),
    }


class LaneConfigBody(BaseModel):
    params: Dict[str, float]


@router.patch("/config/lanes/{lane_id}")
def patch_lane_config(lane_id: str, body: LaneConfigBody) -> Dict[str, Any]:
    """修改车道参数（白名单 + 范围校验；未知键直接拒绝，不静默忽略）。"""
    from backend.services import lane_registry as reg

    lane = reg.get_lane(lane_id)
    if not lane:
        raise HTTPException(status_code=404, detail=f"车道不存在: {lane_id}")
    bad = [k for k in body.params if k not in LANE_CONFIG_KEYS]
    if bad:
        raise HTTPException(status_code=400, detail=f"不可编辑的参数: {', '.join(bad)}")
    for k, v in body.params.items():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"参数 {k} 必须是数字") from None
        if fv != fv or fv in (float("inf"), float("-inf")):
            raise HTTPException(status_code=400, detail=f"参数 {k} 不是有限数")
        if k.endswith("_bp") and fv < 0:
            raise HTTPException(status_code=400, detail=f"参数 {k} 不能为负")
        if k.endswith("_ratio") and not (0.0 < fv <= 1.0):
            raise HTTPException(status_code=400, detail=f"参数 {k} 必须在 (0,1]")
    meta = dict(lane.get("meta") or {})
    params = dict(meta.get("params") or {})
    params.update({k: float(v) for k, v in body.params.items()})
    meta["params"] = params
    try:
        reg.update_meta(lane_id, meta)
    except AttributeError:
        # 兼容旧签名：无 update_meta 时回落到 status 写入（不阻断参数生效）
        logger.warning("[TradingHub] lane_registry 无 update_meta，参数未持久化")
        raise HTTPException(status_code=501, detail="车道参数持久化未实现") from None
    return {"ok": True, "lane_id": lane_id, "params": params, "as_of": _now_iso()}
