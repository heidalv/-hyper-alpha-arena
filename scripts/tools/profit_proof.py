"""盈利证明：把实测的「单位名义 edge」换算到不同资金规模。

用途：回答"这个策略到底能不能赚钱"这个问题。
    净额 = 成交率 × 每腿名义 × 均 net_bp / 1e4
其中「每腿名义」受资金规模约束（风险预算按权益计算），
所以资金越大，同样 edge 下的绝对收益越高。

本工具**只用实测数据**（lane_ledger），不做任何假设性外推；
唯一的外推维度是"若资金放大 N 倍，名义也会放大 N 倍"
（依据：`notional_cap_usd` = equity × loss_frac / stop，与权益成正比）。

用法：
    python scripts/tools/profit_proof.py
    python scripts/tools/profit_proof.py --since "2026-10-05 15:57:56+08"
"""
from __future__ import annotations

import argparse
import io
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
DSN = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"

# 策略前两次修复的部署时间（之后才是"当前策略"的干净样本）
DEFAULT_SINCE = "2026-10-05 15:57:56+08"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=DEFAULT_SINCE)
    ap.add_argument("--equity", type=float, default=None,
                    help="当前权益（默认从 lane_runtime 读不到就用 301）")
    args = ap.parse_args()

    with psycopg.connect(DSN, autocommit=True) as conn:
        cur = conn.cursor()

        # ── 实测：干净窗口的成交率 / 名义 / edge ──────────────────
        cur.execute(f"""
            SELECT count(*),
                   EXTRACT(EPOCH FROM (max(ts)-min(ts)))/3600.0,
                   coalesce(avg(notional),0),
                   coalesce(avg(net_bp),0),
                   coalesce(sum(notional*net_bp/1e4),0),
                   coalesce(sum(notional),0),
                   count(*) FILTER (WHERE fee_bp < -0.5),
                   coalesce(sum(notional*fee_bp/1e4),0)
            FROM lane_ledger
            WHERE lane_id='mm_asterdex' AND event='fill'
              AND ts >= '{args.since}'::timestamptz
        """)
        legs, hrs, avg_notl, avg_net_bp, net_usd, tot_notl, taker, fee_usd = cur.fetchone()
        hrs = float(hrs or 0.0)
        legs = int(legs or 0)

        print("=" * 84)
        print("盈利证明 —— 基于实测成交账本（lane_ledger）")
        print("=" * 84)
        print(f"  样本窗口起点   : {args.since}")
        if legs == 0:
            print("  窗口内无成交 —— 无法证明。请换一个活跃时段。")
            return 0
        print(f"  腿数           : {legs}")
        print(f"  跨度           : {hrs:.2f} 小时（{hrs*60:.0f} 分钟）")
        rate = legs / hrs if hrs > 0 else 0.0
        print(f"  成交率         : {rate:.1f} 腿/小时")
        print(f"  平均名义/腿     : ${float(avg_notl):,.0f}")
        print(f"  平均 net_bp/腿  : {float(avg_net_bp):+.2f} bp")
        print(f"  taker 腿        : {int(taker)}")
        print(f"  手续费          : ${float(fee_usd):.4f}")
        print(f"  实测净额        : ${float(net_usd):+.4f}")

        # ── 资金效率：每万名义净额 ────────────────────────────────
        per10k = float(net_usd) / max(float(tot_notl), 1e-9) * 1e4
        print()
        print("=" * 84)
        print("资金效率（不随规模变的量 —— 这才是「策略是否成立」的判据）")
        print("=" * 84)
        print(f"  总名义          : ${float(tot_notl):,.0f}")
        print(f"  每万名义净额     : {per10k:+.2f} 美元/万美元名义")
        print(f"  每腿净额         : ${float(net_usd)/legs:+.4f}")

        # ── [整顿轮·T32 2026-10-06] 统计显著性 ───────────────────
        # 为什么必须有：edge 很薄时（实测探针档 μ=+0.90bp、σ=8.22bp、信噪比 0.11）
        # 逐小时盈亏是**噪声主导**，看总净额会得出错误结论。
        # 只有 t = μ/(σ/√n) ≥ 1.96 才能说"正 edge 得到 95% 置信支持"。
        print()
        print("=" * 84)
        print("统计显著性（判断「是否真的赚」——薄 edge 必须看这个）")
        print("=" * 84)
        cur.execute("""
            SELECT coalesce(net_bp,0) FROM lane_ledger
            WHERE lane_id='mm_asterdex' AND event='fill'
              AND ts >= %s::timestamptz
              AND coalesce(meta_json->>'flatten','false')='false'
        """, (args.since,))
        vals = [float(r[0]) for r in cur.fetchall()]
        if len(vals) >= 5:
            import statistics as _st
            mu = _st.mean(vals)
            sd = _st.pstdev(vals) or 1e-9
            n = len(vals)
            se = sd / (n ** 0.5)
            t = mu / se if se > 0 else 0.0
            lo, hi = mu - 1.96 * se, mu + 1.96 * se
            print(f"  进场腿 n        : {n}")
            print(f"  均 net_bp (μ)   : {mu:+.3f}")
            print(f"  标准差 (σ)      : {sd:.2f} bp")
            print(f"  标准误 (σ/√n)   : {se:.3f}")
            print(f"  **t 值**        : **{t:+.2f}**   （≥1.96 ⇒ 95% 置信下 μ>0）")
            print(f"  95% 置信区间    : [{lo:+.3f}, {hi:+.3f}] bp")
            if mu > 0 and t < 1.96:
                need = (1.96 * sd / mu) ** 2
                rate = n / max(float(hrs or 0), 1e-9)
                print(f"  **样本不足**：需要 n ≈ {need:,.0f} 条"
                      f"（当前 {n}）")
                if rate > 0:
                    print(f"    按当前 {rate:.1f} 条/小时 ⇒ 还需 "
                          f"≈ {(need-n)/rate/24:,.1f} 天")
            elif mu > 0:
                print("  ✅ 正 edge 已达 95% 置信")
            else:
                print("  ❌ μ ≤ 0 —— 样本再多也证明不了正 edge")
        else:
            print(f"  进场腿仅 {len(vals)} 条，不足以做显著性检验")

        # ── 规模换算（唯一的外推：名义与权益成正比）─────────────
        eq = float(args.equity or 301.0)
        print()
        print("=" * 84)
        print("规模换算（依据：notional_cap_usd = equity × loss_frac / stop，与权益成正比）")
        print("=" * 84)
        print(f"  当前权益 = ${eq:,.0f}")
        print()
        print(f"  {'权益':>12}{'倍数':>7}{'年化净额':>13}{'日净额':>11}{'月净额':>11}")
        for mult in (1, 3, 10, 30, 100, 300, 1000):
            cap = eq * mult
            per_leg = per10k * float(avg_notl) * mult / 1e4
            daily = per_leg * rate * 24
            print(f"  ${cap:>11,.0f}{mult:>7}x{daily*365:>13,.0f}"
                  f"{daily:>11,.2f}{daily*30:>11,.0f}")

        print()
        print("  读法（**已按 T32 修正，此前这里是过度声明**）：")
        print("   · 「每万名义净额」是**点估计**，规模无关；")
        print("   · **但点估计为正 ≠ 已证明为正** —— 必须同时看上面的 t 值/置信区间；")
        print("   · 只有 **t ≥ 1.96** 才能说「正 edge 在 95% 置信下成立」；")
        print("   · 否则只能说「尚未在统计上区分出盈利与零」。")
        print()
        print("  ⚠️⚠️ 四条必须同时看的限制（否则会得出荒谬结论）")
        print("  ① 窗口极短：上表的日/月/年是**按当前短窗口的速率线性外推**。")
        print(f"     本窗口只有 {hrs:.2f} 小时、{legs} 腿，市场状态单一，")
        print("     把它当稳定速率会显著高估。真实需要**跨多日**样本。")
        print("  ② 无冲击成本：完全没建模市场冲击 / 深度限制。")
        print("     资金放大后我们自己的挂单会变成流动性，edge 必然衰减。")
        print("  ③ 杠杆与容量上限：名义与权益成正比只在**容量未饱和**时成立。")
        print("     超过交易所杠杆/保证金上限后不再线性。")
        print("  ④ 成交判定精度（最致命）：模拟盘只有**桶级**数据")
        print("     （low/high/taker_buy/sell）。`runner.py:1756-1762` 自己写明")
        print("     无法判断桶内是否真有成交落在我们价位，")
        print("     代码注释提到实测 **47.7% 的成交价在真实市场找不到对应逐笔**。")
        print("     ⇒ 上述净额是**乐观上界**，不是可实现收益。")
        print()
        print("  结论的正确用法：")
        print("   ✅ 用「每万名义净额 > 0」判断**策略方向是否成立**")
        print("   ❌ 不要用上表的日/月/年数字做任何资金规划")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
