"""诊断：套利中心页面上那些自相矛盾的数字分别来自哪里。

[F219 2026-09-15] 现场（用户截图，21:57:44）
--------------------------------------------------
   组合权益 $300.00   今日 −$12.67 (−4.22%)   风险预算 60%/$180   跑道 0/5
   统一模拟账户「MM 做市专用 $300」: 权益 $300.00  可用 $223.84  已实现 +$12.70
   按策略分账 MM: 净额 −$77.03  笔数 $0.00  资金 $300.00
   交易所预算 asterdex: 分配 $300.00 · 可用 $223.84
   L1 车道: 净期望 +1.196bp(n=285)  今日 −$12.67  近7天净收益 +$1,528.74  名义 $28,000+  56%
⇒ 同一屏里 权益/可用/已实现/今日 四个数互不相容 ✗，且 7 天 +$1,528 在 $300 账户上不可能 ✗。
本脚本把这些数各自的**来源接口/表**打出来，用于定位而不是猜 ✓。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from backend.database.connection import SessionLocal  # noqa: E402

BASE = "http://127.0.0.1:8000/api"
LANE = "mm_asterdex"


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read())


def show(name: str, obj, keys=None, depth: int = 0) -> None:
    pad = "  " * depth
    if isinstance(obj, dict):
        if keys:
            print(f"{pad}{name}: " + json.dumps({k: obj.get(k) for k in keys},
                                                ensure_ascii=False, default=str))
        else:
            print(f"{pad}{name}: " + json.dumps(obj, ensure_ascii=False, default=str)[:400])
    else:
        print(f"{pad}{name}: {obj}")


print("=" * 100)
print(f"诊断 @ {datetime.now().strftime('%H:%M:%S')}")
print("=" * 100)

# ① 统一账户（页面「统一模拟账户」卡片 + 交易所预算行）
u = get("/trading/account/unified?days=30")
print("\n【① /trading/account/unified】顶层键:", list(u.keys()))
show("account", u.get("account"))
st = u.get("strategies") or []
for r in st:
    show("strategies[]", r, ["strategy_type", "net_usd", "pnl_usd", "fee_usd",
                             "capital_usd", "fills", "notional"])
for e in (u.get("exchanges") or [])[:3]:
    show("exchanges[]", e, ["exchange", "allocated_usd", "available_usd", "frozen_usd"])

# ② 组合汇总（顶部四个 KPI）
s = get("/trading/portfolio/summary")
print("\n【② /trading/portfolio/summary】")
for k in ("equity", "equity_source", "pnl_today_usd", "pnl_today_pct", "pnl_7d_usd",
          "pnl_7d_pct", "budget_used_usd", "budget_used_pct", "lanes_active", "lanes_total",
          "fills_today", "notional_today", "as_of"):
    show(k, s, [k] if False else None) if False else None
print("  " + json.dumps({k: s.get(k) for k in ("equity", "equity_source", "pnl_today_usd",
                                               "pnl_today_pct", "pnl_7d_usd", "pnl_7d_pct",
                                               "budget_used_usd", "budget_used_pct",
                                               "lanes_active", "lanes_total",
                                               "fills_today", "notional_today", "as_of")},
                        ensure_ascii=False, default=str))

# ③ 车道列表（L1 卡片上的 今日/近7天/名义/净期望）
lanes = get("/trading/lanes")
for it in (lanes.get("items") or []):
    if it.get("lane_id") != LANE:
        continue
    print("\n【③ /trading/lanes → L1】")
    print("  " + json.dumps({k: it.get(k) for k in (
        "lane_id", "mode", "status", "pnl_today_usd", "fills_today", "pnl_7d_usd",
        "fills_7d", "notional_7d", "inventory_usd", "net_exposure_usd", "updated_at",
        "data_age_sec")}, ensure_ascii=False, default=str))
    e = it.get("edge") or {}
    print("  edge: " + json.dumps({k: e.get(k) for k in (
        "net_bp", "n", "source", "as_of", "gross_bp", "cost_bp", "spread_bp", "price_bp",
        "max_dd_pct", "fill_rate_ratio")}, ensure_ascii=False, default=str))
    show("  promotion", it.get("promotion"), ["progress_pct", "ready", "passed", "failed"])

# ④ 归因 / 日序列（7 天数字的来源嫌疑）
a = get(f"/trading/portfolio/attribution?days=7")
print("\n【④ /trading/portfolio/attribution?days=7】")
print("  by_lane: " + json.dumps(a.get("by_lane"), ensure_ascii=False, default=str)[:500])
print("  totals: " + json.dumps({k: a.get(k) for k in a.keys() if k != "by_lane"},
                                ensure_ascii=False, default=str)[:400])

# ⑤ 影子报告（模型口径，可能是"净期望"的来源）
try:
    sr = get(f"/trading/lanes/{LANE}/shadow/report?days=7")
    print("\n【⑤ /shadow/report?days=7（模型口径）】")
    print("  " + json.dumps({k: sr.get(k) for k in (
        "window_days", "fills", "notional", "net_usd", "net_bp", "as_of",
        "maker_net_bp", "flatten_net_bp", "max_dd_pct")}, ensure_ascii=False, default=str))
except Exception as e:
    print("\n【⑤ shadow/report】失败:", e)

# ⑥ 账本口径：新时代 vs 7 天（真金白银）
with SessionLocal() as db:
    db.execute(text("SET statement_timeout = 40000"))
    rows = [dict(r) for r in db.execute(text("""
        SELECT
          (SELECT stats_since FROM lane_registry WHERE lane_id=:l) AS stats_since
    """), {"l": LANE}).mappings().all()]
    row = [dict(r) for r in db.execute(text("""
        SELECT total_equity, available_balance, realized_pnl, updated_at, name
        FROM arbitrage_paper_accounts WHERE id=101""")).mappings().all()]
    era = [dict(r) for r in db.execute(text("""
        SELECT count(*) AS n, round(sum(notional)::numeric,2) AS ntl,
               round(sum(net_bp*notional/1e4)::numeric,4) AS net_usd,
               round((sum(net_bp*notional)/NULLIF(sum(notional),0)*1e4)::numeric,4) AS net_bp,
               min(ts) AS first_ts, max(ts) AS last_ts
        FROM lane_ledger WHERE lane_id=:l AND event='fill'
          AND ts >= (SELECT stats_since FROM lane_registry WHERE lane_id=:l)"""),
        {"l": LANE}).mappings().all()]
    d7 = [dict(r) for r in db.execute(text("""
        SELECT count(*) AS n, round(sum(notional)::numeric,2) AS ntl,
               round(sum(net_bp*notional/1e4)::numeric,4) AS net_usd
        FROM lane_ledger WHERE lane_id=:l AND event='fill'
          AND ts >= now() - interval '7 days'"""), {"l": LANE}).mappings().all()]
print("\n【⑥ 账本/账户（真值）】")
print("  account(101):", row)
print("  stats_since:", rows[0]["stats_since"] if rows else None)
print("  era(自 stats_since):", era)
print("  7d(自 now-7d):", d7)
