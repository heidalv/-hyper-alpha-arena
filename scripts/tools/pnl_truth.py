"""盈利基线 / 立规验收工具。

这是"盈利为证"的**唯一度量入口**。任何整改都必须用它的输出前后对比。

用法：
    python scripts/tools/pnl_truth.py              # 打印当前基线
    python scripts/tools/pnl_truth.py --days 7     # 只看近 7 天

输出三块：
  A. 做市车道（mm_asterdex）真实净额 six-dim 分解
  B. 学习账本（trade_facts）净额
  C. 手续费真相：实测费率 vs 配置费率
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import psycopg  # noqa: E402

DSN = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"


def h(t: str) -> None:
    print("\n" + "=" * 78)
    print(t)
    print("=" * 78)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0, help="只统计最近 N 天（0=全部）")
    args = ap.parse_args()
    since = f"AND ts > now() - interval '{args.days} days'" if args.days else ""

    with psycopg.connect(DSN, autocommit=True) as conn:
        cur = conn.cursor()
        print(f"报告范围：{'全部历史' if not args.days else f'最近 {args.days} 天'}")

        # ── A. 做市车道 ──────────────────────────────────────────
        h("A. 做市车道 mm_asterdex —— 六维净额分解（唯一真实成交账本 lane_ledger）")
        cur.execute(f"""
            SELECT count(*) fills, count(DISTINCT symbol) syms,
                   coalesce(sum(notional),0),
                   coalesce(sum(notional*spread_bp/1e4),0),
                   coalesce(sum(notional*price_bp/1e4),0),
                   coalesce(sum(notional*fee_bp/1e4),0),
                   coalesce(sum(notional*funding_bp/1e4),0),
                   coalesce(sum(notional*slippage_bp/1e4),0),
                   coalesce(sum(notional*net_bp/1e4),0)
            FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill' {since}
        """)
        r = cur.fetchone()
        print(f"  成交 {r[0]:,} 笔 / {r[1]} 币 / 名义 ${r[2]:,.0f}")
        print(f"  {'价差捕获 spread':<22} ${r[3]:>10.2f}   ← 做市的毛收入")
        print(f"  {'行情漂移 price':<22} ${r[4]:>10.2f}   ← 库存被市场推动（真正的风险源）")
        print(f"  {'手续费 fee':<22} ${r[5]:>10.2f}   ← taker 平仓付出")
        print(f"  {'资金费 funding':<22} ${r[6]:>10.2f}")
        print(f"  {'滑点 slippage':<22} ${r[7]:>10.2f}")
        print(f"  {'─'*22}  {'─'*11}")
        print(f"  {'净额 NET':<22} ${r[8]:>10.2f}")
        # 每小时速率
        cur.execute(f"""
            SELECT EXTRACT(EPOCH FROM (max(ts)-min(ts)))/3600.0
            FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill' {since}
        """)
        hrs = float(cur.fetchone()[0] or 0) or 1.0
        print(f"\n  跨度 {hrs:,.1f} 小时 ⇒ 净额速率 ${r[8]/hrs:+.3f}/小时"
              f"（${r[8]/hrs*24:+.2f}/天）")

        # 强平 vs 正常腿
        h("A2. 强平腿 vs 正常腿（meta_json->>'flatten'）—— 亏损集中点")
        cur.execute(f"""
            SELECT coalesce(meta_json->>'flatten','?') fl, count(*),
                   round(avg(coalesce(fee_bp,0))::numeric,3),
                   round(avg(coalesce(net_bp,0))::numeric,3),
                   round(sum(notional*net_bp/1e4)::numeric,2)
            FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill' {since}
            GROUP BY 1 ORDER BY 2 DESC
        """)
        print(f"  {'flatten':<10}{'笔数':>8}{'均fee_bp':>10}{'均net_bp':>10}{'净$':>12}")
        for x in cur.fetchall():
            print(f"  {str(x[0]):<10}{x[1]:>8}{str(x[2]):>10}{str(x[3]):>10}{str(x[4]):>12}")

        # maker / taker
        h("A3. maker vs taker 腿")
        cur.execute(f"""
            SELECT CASE WHEN coalesce(fee_bp,0) < -0.5 THEN 'taker'
                        WHEN coalesce(fee_bp,0) > 0.5 THEN 'rebate'
                        ELSE 'maker' END cls,
                   count(*), round(sum(notional)::numeric,0),
                   round(sum(notional*net_bp/1e4)::numeric,2),
                   round(sum(notional*price_bp/1e4)::numeric,2),
                   round(sum(notional*spread_bp/1e4)::numeric,2),
                   round(sum(notional*fee_bp/1e4)::numeric,2)
            FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill' {since}
            GROUP BY 1 ORDER BY 2 DESC
        """)
        print(f"  {'类':<8}{'笔数':>8}{'名义':>13}{'净$':>10}{'行情$':>10}{'价差$':>10}{'费$':>9}")
        for x in cur.fetchall():
            print(f"  {x[0]:<8}{x[1]:>8}{x[2]:>13,}{str(x[3]):>10}{str(x[4]):>10}"
                  f"{str(x[5]):>10}{str(x[6]):>9}")

        # ── B. 学习账本 ─────────────────────────────────────────
        h("B. 学习账本 trade_facts —— 净额口径（net_pnl = pnl − fees）")
        cur.execute("""
            SELECT tier, count(*),
                   round(sum(pnl)::numeric,2),
                   round(sum(fees)::numeric,2),
                   round(sum(coalesce(net_pnl, pnl - coalesce(fees,0)))::numeric,2)
            FROM trade_facts GROUP BY 1 ORDER BY 1
        """)
        print(f"  {'tier':<8}{'n':>7}{'Σ毛pnl':>13}{'Σ费':>10}{'Σ净net':>13}")
        tot = [0, 0.0, 0.0, 0.0]
        for x in cur.fetchall():
            print(f"  {x[0]:<8}{x[1]:>7}{str(x[2]):>13}{str(x[3]):>10}{str(x[4]):>13}")
            tot[0] += x[1]; tot[1] += float(x[2]); tot[2] += float(x[3]); tot[3] += float(x[4])
        print(f"  {'合计':<8}{tot[0]:>7}{tot[1]:>13.2f}{tot[2]:>10.2f}{tot[3]:>13.2f}")
        cur.execute("SELECT count(*) FROM trade_facts_quarantine")
        print(f"\n  隔离（坏行）={cur.fetchone()[0]} 行  ← 已从学习输入剔除")

        # ── C. 手续费真相 ───────────────────────────────────────
        h("C. 手续费真相：实测 vs 配置")
        cur.execute("""
            SELECT fee_bp, count(*), round(sum(notional)::numeric,0)
            FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'
            GROUP BY 1 ORDER BY 2 DESC LIMIT 6
        """)
        print("  实测 fee_bp 档位:")
        for x in cur.fetchall():
            print(f"    fee_bp={x[0]:>10}  笔数={x[1]:>7}  名义=${x[2]:>12,}")
        try:
            from backend.services.fee_schedule_service import (
                get_fee_rate, break_even_move_bp, round_trip_cost_rate,
            )
            print("\n  配置费率（唯一真相源 fee_schedule_service）:")
            for ex in ("asterdex", "binance", "hyperliquid"):
                print(f"    {ex:<12} maker={get_fee_rate(ex,True)*1e4:>5.2f}bp "
                      f"taker={get_fee_rate(ex,False)*1e4:>5.2f}bp")
            print("\n  盈亏平衡（必须动多少才不亏）:")
            for lbl, kw in (
                ("双挂单（做市本命）", dict(exchange="asterdex", entry_is_maker=True,
                                     exit_is_maker=True, hold_seconds=60)),
                ("挂进吃出", dict(exchange="asterdex", entry_is_maker=True,
                                exit_is_maker=False, hold_seconds=60)),
                ("吃进吃出", dict(exchange="asterdex", entry_is_maker=False,
                                exit_is_maker=False, hold_seconds=60)),
                ("吃进吃出+止损", dict(exchange="asterdex", entry_is_maker=False,
                                    exit_is_maker=False, hold_seconds=3600,
                                    is_stop_loss=True)),
            ):
                print(f"    {lbl:<20} {break_even_move_bp(**kw):>7.2f} bp")
        except Exception as e:  # noqa: BLE001
            print(f"  （费率模块不可用: {e}）")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
