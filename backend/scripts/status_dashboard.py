# -*- coding: utf-8 -*-
"""[调研轮29 2026-09-17] **系统状态面板**（只读，一行命令看清全链路）。

输出四块：
  1. 账户与容量：权益/已实现/未实现、并发占用、当前每笔名义与生效上限；
  2. 近 7 天 / 30 天：按车道、按入场来源的净额与胜率（定位钱在哪）；
  3. 近 24h 拦截 Top（定位卡在哪）；
  4. 在途改动与待决事项（人工清单，避免遗忘）。

用法：
  python backend/scripts/status_dashboard.py
  python backend/scripts/status_dashboard.py --account 14
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _src(sid: str) -> str:
    s = str(sid or "-")
    for p, name in (("tpl_", "tpl_模板"), ("ai_auto_", "ai_auto_AI选币"), ("auto_", "auto_全自动"),
                    ("trend_e1", "trend_e1"), ("scalp", "scalp_遗留"), ("gen_", "gen_生成")):
        if s.startswith(p):
            return name
    return s[:12]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=14)
    args = ap.parse_args()

    from backend.database.connection import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()
    try:
        b = db.execute(text("SELECT total_equity, initial_balance, realized_pnl, unrealized_pnl "
                            "FROM paper_balances WHERE account_id=:a"),
                       {"a": args.account}).mappings().first()
        eq = float(b["total_equity"] or 0)
        init = float(b["initial_balance"] or 0)
        print(f"=== 账户 (acct={args.account}) ===")
        print(f"  权益 {eq:,.2f}（初始 {init:,.0f}，累计 {(eq/init-1)*100:+.2f}%）"
              f" | 已实现 {float(b['realized_pnl'] or 0):+,.2f}"
              f" | 未实现 {float(b['unrealized_pnl'] or 0):+,.2f}")

        caps = {"mid": 0.15, "long": 0.10, "short": 0.35}
        try:
            from backend.services.position_construction import LaneLimits
            for k in caps:
                caps[k] = LaneLimits.for_lane(k).max_weight_per_symbol
        except Exception:
            pass
        print(f"\n=== 容量与持仓（单币名义上限："
              f"{' '.join(f'{k}=${eq*v:,.0f}' for k, v in caps.items())}）===")
        opens = db.execute(text(
            "SELECT id, symbol, timeframe_tier AS tier, leverage, margin, "
            "       size*entry_price AS notional, unrealized_pnl, "
            "       to_char(opened_at,'MM-DD HH24:MI') AS op "
            "FROM paper_positions WHERE account_id=:a AND status='open' ORDER BY id"
        ), {"a": args.account}).mappings().all()
        used = defaultdict(int)
        for r in opens:
            t = str(r["tier"] or "-")
            used[t] += 1
            cap = caps.get(t, 0.35) * eq
            flag = "✅" if float(r["notional"] or 0) <= cap * 1.02 else "⚠️超上限(存量不追溯)"
            print(f"  #{r['id']} {r['symbol']:<7}{t:<5}lev{r['leverage']:<4.1f} "
                  f"保证金={r['margin']:7.2f} 名义={r['notional']:8.0f} {flag} "
                  f"uPnL={float(r['unrealized_pnl'] or 0):+7.2f} {r['op']}")
        try:
            from backend.config.settings import MIDLONG_MAX_OPEN_POSITIONS, MIDLONG_MAX_LONG_LANE_POSITIONS
            print(f"  并发占用：mid {used.get('mid', 0)}/{MIDLONG_MAX_OPEN_POSITIONS} "
                  f"| long {used.get('long', 0)}/{MIDLONG_MAX_LONG_LANE_POSITIONS}")
        except Exception:
            print(f"  并发占用：{dict(used)}")

        for days in (7, 30):
            print(f"\n=== 近 {days} 天：按车道 / 按来源 ===")
            rows = db.execute(text(
                "SELECT timeframe_tier AS tier, coalesce(strategy_id,'-') AS sid, "
                "       unrealized_pnl AS pnl, close_reason "
                "FROM paper_positions WHERE account_id=:a AND status='closed' "
                "  AND closed_at > now() - (:d * interval '1 day')"
            ), {"a": args.account, "d": days}).mappings().all()
            g = defaultdict(list)
            for r in rows:
                g[str(r["tier"])].append(r)
                g[_src(r["sid"])].append(r)
            for k, v in sorted(g.items(), key=lambda kv: sum(float(x["pnl"] or 0) for x in kv[1])):
                if len(v) < 3:
                    continue
                pnls = [float(x["pnl"] or 0) for x in v]
                print(f"  {k:<16}n={len(v):<4}净={sum(pnls):+9.2f} 均={sum(pnls)/len(pnls):+7.2f} "
                      f"胜率={sum(1 for p in pnls if p>0)/len(pnls)*100:3.0f}%")
    finally:
        db.close()

    from backend.services.mlto.midlong_direction_audit import _iter_rows, audit_paths
    rows = [r for r in _iter_rows(audit_paths()) if float(r.get("epoch") or 0) >= time.time() - 86400]
    print(f"\n=== 近 24h 拦截 Top（共 {len(rows)} 行）===")
    c = Counter(str(r.get("reason") or "-").split(":")[0][:34]
                for r in rows if str(r.get("outcome")) == "skip")
    for k, v in c.most_common(8):
        print(f"  {k:<38}{v}")
    print(f"  outcome: {dict(Counter(str(r.get('outcome')) for r in rows))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
