# -*- coding: utf-8 -*-
"""H216 腿量上限的反事实：给「队列份额腿量」加硬顶会怎样。

# 机制（已核实）

`runner.py:774`：

    qty = min(_target_qty, _avail * _QUEUE_SHARE)      # _QUEUE_SHARE = 0.30
    _avail = 该 15s 桶的**主动成交量**

⇒ 腿量 = min(目标腿量, **该桶主动成交量 × 0.30**)。
**没有任何绝对上限。** 某个 15s 桶里有 $22,600 的主动卖 ⇒ 我们吃 $6,789。

实测后果（H215）：

    名义分位   P50 $163　P99 $861　P99.9 $2,000　max $11,661（= 71.6× 中位）
    集中度     前 10 腿 = 全部亏损的 **41%**；前 50 腿 = **62%**；前 100 腿 = **71%**
    逐日前 1%  占比 29%~161%，12 天里 11 天为负

⇒ **行情越剧烈，我们的腿越大**——而剧烈行情正是 `price_bp` 最差的地方。
这是一个**逆选择放大器**，不是信号缺失。

# 反事实口径

把每条腿的美元盈亏按 `k_i = min(1, cap / notional_i)` 缩放（进场腿与出场腿
按同一比例缩放 ⇒ 仓位等比例变小，往返的 bp 不变，美元盈亏按 `k_i` 缩放）。
`cap` 用**中位腿量的倍数**表示（中位腿量是引擎的"正常"腿，随权益复利变化）。

**必须同时看两面**：上限也会砍掉**赚大钱**的腿（表里有 +29bp、+59bp 的大腿）。
因此判据是：**净额改善** 且 **最差单日不恶化** 才算有效。

# 用法

    python scripts/h216_leg_cap_counterfactual.py --days 14
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h216_leg_cap_counterfactual.json"


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT to_char(ts AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD') AS d,
                       coalesce(notional,0), coalesce(net_bp,0),
                       coalesce(meta_json->>'flatten','false'),
                       coalesce(symbol,'')
                FROM lane_ledger
                WHERE lane_id=%s AND ts >= now() - (%s || ' days')::interval
                  AND coalesce(notional,0) > 0
                ORDER BY ts ASC
            """, (LANE, str(int(a.days))))
            rows = cur.fetchall()

    if not rows:
        print("无数据")
        return 1

    N = [float(r[1]) for r in rows]
    B = [float(r[2]) for r in rows]
    U = [n * b / 1e4 for n, b in zip(N, B)]
    med = st.median(N)
    tot = sum(U)
    days = {}
    for i, r in enumerate(rows):
        days.setdefault(r[0], []).append(i)

    print("=" * 104)
    print("H216  腿量硬上限的反事实")
    print("=" * 104)
    print(f"\n  {len(rows)} 腿　中位腿量 ${med:.2f}　名义合计 ${sum(N):,.0f}　"
          f"原净额 **${tot:+.2f}**")
    print(f"  最大腿 ${max(N):,.2f} = {max(N)/med:.1f}× 中位")

    CAPS = [1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 16.0, 999.0]
    print(f"\n{'━'*104}\n  一、按「中位腿量的倍数」设上限\n{'━'*104}")
    print(f"\n  {'上限':>10}{'美元上限':>12}{'被截腿数':>10}{'被截名义%':>12}"
          f"{'净额$':>13}{'vs 原':>11}{'最差单日$':>13}{'正日数':>8}")
    print("  " + "-" * 98)
    res = []
    base_worst = min(sum(U[i] for i in ii) for ii in days.values())
    base_pos = sum(1 for ii in days.values() if sum(U[i] for i in ii) > 0)
    for capm in CAPS:
        cap = med * capm
        Uc = [u * min(1.0, cap / n) for u, n in zip(U, N)]
        ncut = sum(1 for n in N if n > cap)
        notl_cut = sum(n - cap for n in N if n > cap) / sum(N) * 100
        net = sum(Uc)
        daynets = [sum(Uc[i] for i in ii) for ii in days.values()]
        worst = min(dnet for dnet, _ii in zip(daynets, days.values()))
        npos = sum(1 for x in daynets if x > 0)
        tag = "（无上限=原状）" if capm >= 999 else ""
        print(f"  {capm:>9.1f}×{cap:>12,.0f}{ncut:>10}{notl_cut:>11.1f}%"
              f"{net:>+13.2f}{net-tot:>+11.2f}{worst:>+13.2f}{npos:>8}{tag}")
        res.append({"cap_mult": capm, "cap_usd": round(cap, 2), "cut_legs": ncut,
                    "cut_notional_pct": round(notl_cut, 2), "net": round(net, 2),
                    "delta": round(net - tot, 2), "worst_day": round(worst, 2),
                    "pos_days": npos})

    print(f"\n  基准：最差单日 ${base_worst:+.2f}　正日数 {base_pos}/{len(days)}")

    # ── 二、被砍掉的是"赚"还是"亏" ──
    print(f"\n{'━'*104}\n  二、被上限截掉的那部分：是净亏还是净赚\n{'━'*104}")
    print(f"\n  {'上限':>10}{'截掉部分原净额$':>18}{'截掉部分的 bp 均值':>22}"
          f"{'截掉腿数':>10}")
    for capm in (1.0, 2.0, 3.0, 5.0, 8.0):
        cap = med * capm
        sel = [i for i, n in enumerate(N) if n > cap]
        if not sel:
            continue
        cut_usd = sum(U[i] - U[i] * cap / N[i] for i in sel)
        wb = sum(B[i] * (N[i] - cap) for i in sel) / sum(N[i] - cap for i in sel)
        print(f"  {capm:>9.1f}×{cut_usd:>+18.2f}{wb:>+22.4f}{len(sel):>10}")
    print(f"\n  ⇒ 「截掉部分原净额」为**负** ⇒ 上限砍掉的是亏损 ⇒ 上限有效（这显然）")
    print(f"     但**必须配合上表**：若截掉部分里也含大额盈利腿，"
          f"上表的『vs 原』才是真正的判据。")

    # ── 三、逐日对比：3× 中位 vs 原状 ──
    print(f"\n{'━'*104}\n  三、逐日：3× 中位上限 vs 原状\n{'━'*104}")
    cap = med * 3.0
    print(f"\n  {'日期':<12}{'腿数':>7}{'原净$':>12}{'3×上限后$':>13}{'差$':>11}"
          f"{'被截腿':>9}{'该日最大腿/中位':>16}")
    print("  " + "-" * 84)
    same_sign = 0
    ndays = 0
    for d in sorted(days):
        ii = days[d]
        o = sum(U[i] for i in ii)
        nw = sum(U[i] * min(1.0, cap / N[i]) for i in ii)
        cut = sum(1 for i in ii if N[i] > cap)
        mx = max(N[i] for i in ii)
        ndays += 1
        if (o > 0) == (nw > 0):
            same_sign += 1
        print(f"  {d:<12}{len(ii):>7}{o:>+12.2f}{nw:>+13.2f}{nw-o:>+11.2f}"
              f"{cut:>9}{mx/med:>15.1f}×")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "legs": len(rows), "median_notional": round(med, 2),
        "baseline_net": round(tot, 2), "baseline_worst_day": round(base_worst, 2),
        "baseline_pos_days": base_pos, "n_days": len(days), "caps": res,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
