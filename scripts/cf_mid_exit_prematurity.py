"""[2026-09-20] 中线机械出场"是否砍早了" —— **边际反事实**（只读，模型面最小）。

为什么不用全量回放：`scripts/replay_mid_exit_counterfactual.py` 第一版把出场规则按记忆编码，
基线算出 +$132 而实际是 −$16.91（差一个量级）⇒ 规则编码不可信。与其继续加规则，
不如把问题缩小到**不可能算错**的那一步：

  对**实际由机械规则平掉**的单（sl / breakeven_tp / tp / trailing_callback / sl_pct / staged_tp2_clear），
  只看**出场之后**的行情：如果当时不平，价格接下来朝有利方向还能走多少（post-MFE）、
  朝不利方向会走多少（post-MAE）。这不需要编码任何出场规则——只需要"出场时刻 + 之后的价格路径"，
  而这两样都有独立证据（DB 的 closed_at 是事实，行情来自 `alpha_market`）。

口径与假设（写清楚，避免又一次"算得漂亮但错"）：
  - 时区：持仓时间 = CST，行情 epoch = UTC，换算 +8h（阶段1 已用 UTC/CST 对照验证过）。
  - 路径来源：主用 `market_trades_aggregated` 15s 成交桶（真实成交 high/low），
    并用 1m K 线（binance）复算一遍作为**独立复现**；两者结论不一致就不许下结论。
  - 观察窗口：出场后 min(48h, 原 time_limit 剩余)，默认 48h；超出该窗口的"本可持有"不可见。
  - 不含手续费与资金费（比较的是同一笔的**差额**，费用两边同样计，故对结论影响小；
    但会把实际费率单列出来供参考）。

用法：
  .venv\\Scripts\\python.exe scripts/cf_mid_exit_prematurity.py --days 30
  .venv\\Scripts\\python.exe scripts/cf_mid_exit_prematurity.py --days 30 --source kline   # 独立复现
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import statistics as st
import sys
from typing import Dict, List, Optional

import psycopg

ARENA = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
CST = dt.timezone(dt.timedelta(hours=8))
MECH = {"sl", "tp", "breakeven_tp", "exit_policy:sl_pct", "exit_policy:trailing_callback",
        "staged_tp2_clear"}
# 长线(tier=long)专属机械/半机械出场通道（2026-09-23 调研用）
EXTRA_MECH = {"emergency_drawdown", "profit_drawdown_stage", "profit_drawdown_full",
              "profit_drawdown_hard", "trend_review_close"}


def load_trades(days: str, tier: str = "mid") -> List[Dict]:
    with psycopg.connect(ARENA, autocommit=True) as c:
        cur = c.cursor()
        cur.execute("SET app.is_admin='on'")
        cur.execute(
            """select id, symbol, side, entry_price, size, leverage, opened_at, closed_at, close_price,
                      close_reason, peak_pnl_pct, trough_pnl_pct, sl_price, tp_price, exit_state_json,
                      coalesce(partial_realized_pnl,0)+coalesce(unrealized_pnl,0) pnl_usd,
                      coalesce(partial_fee_paid,0) fee_usd
               from paper_positions
               where account_id=14 and status='closed' and timeframe_tier=%s
                 and closed_at > now() - (%s || ' days')::interval
               order by closed_at""",
            (tier, days),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def bars_after(cur, symbol: str, exchange: str, t0: int, t1: int, source: str):
    if source == "agg":
        cur.execute(
            """select timestamp, high_price, low_price, vwap from market_trades_aggregated
               where symbol=%s and exchange=%s and timestamp between %s and %s order by timestamp""",
            (symbol, exchange, t0 * 1000, t1 * 1000),
        )
        return [(int(r[0]) // 1000, float(r[3] or r[1]), float(r[1]), float(r[2]), float(r[3] or r[1]))
                for r in cur.fetchall()]
    cur.execute(
        """select timestamp, open_price, high_price, low_price, close_price from crypto_klines
           where symbol=%s and exchange=%s and period='1m' and environment='mainnet'
             and timestamp between %s and %s order by timestamp""",
        (symbol, exchange, t0, t1),
    )
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in cur.fetchall()]


def dirret(side: str, entry: float, px: float) -> float:
    r = (px - entry) / entry
    return r if str(side).lower().startswith("l") else -r


def analyze(trades: List[Dict], exchange: str, source: str, horizon_h: float) -> Dict:
    rows: List[Dict] = []
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        for t in trades:
            reason = str(t["close_reason"] or "")
            if not (reason in MECH or reason in EXTRA_MECH or reason.startswith("long_trend_v2:")):
                continue
            o = int(t["opened_at"].replace(tzinfo=CST).timestamp())
            cx = int(t["closed_at"].replace(tzinfo=CST).timestamp())
            st_json = {}
            try:
                st_json = json.loads(t["exit_state_json"] or "{}")
            except Exception:
                pass
            pol = st_json.get("exit_policy") or {}
            tl = float(pol.get("time_limit_sec") or 172800)
            horizon = min(horizon_h * 3600, max(0.0, o + tl - cx))
            if horizon < 600:
                continue
            bars = bars_after(cur, t["symbol"], exchange, cx, int(cx + horizon), source)
            if len(bars) < 3:
                continue
            side, entry = t["side"], float(t["entry_price"])
            lo_hi = [(b[3], b[2]) for b in bars]           # (low, high)
            post_mfe = max(dirret(side, entry, (hi if str(side).lower().startswith("l") else lo))
                           for lo, hi in lo_hi) * 100
            post_mae = min(dirret(side, entry, (lo if str(side).lower().startswith("l") else hi))
                           for lo, hi in lo_hi) * 100
            last_close = bars[-1][4]
            hold_ret = dirret(side, entry, last_close) * 100
            actual_ret = dirret(side, entry, float(t["close_price"])) * 100
            rows.append({
                "id": t["id"], "symbol": t["symbol"], "side": side, "reason": reason,
                "actual_pp": round(actual_ret, 4), "post_mfe_pp": round(post_mfe, 4),
                "post_mae_pp": round(post_mae, 4), "hold_to_horizon_pp": round(hold_ret, 4),
                "delta_hold_pp": round(hold_ret - actual_ret, 4),
                "hours_available": round(horizon / 3600, 1),
                "sl_pct_eff": pol.get("sl_pct"), "pnl_usd": float(t["pnl_usd"] or 0),
                "notional": float(t["size"]) * entry,
            })
    return {"rows": rows, "exchange": exchange, "source": source}


def summarize(res: Dict, exchange: str, source: str) -> Dict:
    rows = res["rows"]
    if not rows:
        return {}
    def block(sel: List[Dict]) -> Dict:
        if not sel:
            return {}
        d = [r["delta_hold_pp"] for r in sel]
        dusd = [r["delta_hold_pp"] / 100.0 * r["notional"] for r in sel]
        return {
            "n": len(sel),
            "actual_total_pp": round(sum(r["actual_pp"] for r in sel), 2),
            "hold_total_pp": round(sum(r["hold_to_horizon_pp"] for r in sel), 2),
            "delta_total_pp": round(sum(d), 2), "delta_mean_pp": round(st.mean(d), 4),
            "delta_total_usd": round(sum(dusd), 2),
            "delta_positive_n": sum(1 for x in d if x > 0),
            "post_mfe_median_pp": round(st.median([r["post_mfe_pp"] for r in sel]), 3),
            "post_mae_median_pp": round(st.median([r["post_mae_pp"] for r in sel]), 3),
        }
    out = {"all_mechanical": block(rows)}
    for reason in sorted(set(r["reason"] for r in rows)):
        out["by_reason:" + reason] = block([r for r in rows if r["reason"] == reason])
    # 亏损侧 vs 盈利侧：出场"砍早了"只对盈利侧有意义
    out["winners(actual>0)"] = block([r for r in rows if r["actual_pp"] > 0])
    out["losers(actual<=0)"] = block([r for r in rows if r["actual_pp"] <= 0])
    # 理想过滤器上界：只在 post-MFE > 门槛时才继续持有（事后视角，不可实现，仅作上界）
    for thr in (0.5, 1.0):
        sel = [r for r in rows if r["post_mfe_pp"] >= thr]
        if sel:
            out["oracle_continue_if_post_mfe>=%.1f" % thr] = block(sel)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="30")
    ap.add_argument("--tier", default="mid")
    ap.add_argument("--exchange", default="binance")
    ap.add_argument("--source", default="agg", choices=("agg", "kline"))
    ap.add_argument("--horizon-hours", type=float, default=48.0)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    trades = load_trades(a.days, a.tier)
    res = analyze(trades, a.exchange, a.source, a.horizon_hours)
    print("样本: mid 已平仓 %d 笔 → 其中机械出场且有后续行情 %d 笔（源=%s:%s，观察窗≤%.0fh）"
          % (len(trades), len(res["rows"]), a.source, a.exchange, a.horizon_hours))
    summary = summarize(res, a.exchange, a.source)
    order = ["all_mechanical", "winners(actual>0)", "losers(actual<=0)"] + \
            [k for k in summary if k.startswith("by_reason")]
    for k in order:
        b = summary.get(k)
        if not b:
            continue
        print("  %-34s n=%-3d 实际合计=%-8s 持有到窗口末=%-8s 差额=%-8s (%+.2f USD) "
              "差额>0 的笔数=%-3d post-MFE中位=%-7s post-MAE中位=%s"
              % (k, b["n"], b["actual_total_pp"], b["hold_total_pp"], b["delta_total_pp"],
                 b["delta_total_usd"], b["delta_positive_n"], b["post_mfe_median_pp"], b["post_mae_median_pp"]))
    print("\n  单笔明细（按差额排序，只看前 12 条）：")
    for r in sorted(res["rows"], key=lambda x: -x["delta_hold_pp"])[:12]:
        print("    id=%-5s %-8s %-6s %-22s 实际=%+.3f%% 持有到窗口末=%+.3f%% 差额=%+.3f%% "
              "postMFE=%+.3f%% postMAE=%+.3f%% 窗口=%sh"
              % (r["id"], r["symbol"], r["side"], r["reason"], r["actual_pp"],
                 r["hold_to_horizon_pp"], r["delta_hold_pp"], r["post_mfe_pp"], r["post_mae_pp"],
                 r["hours_available"]))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump({"summary": summary, "rows": res["rows"]}, fh, ensure_ascii=False, indent=1)
        print("\n写入 %s" % a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
