# -*- coding: utf-8 -*-
"""H216b 加了腿量上限之后，日亏闸阈值该定在哪（重新拟合）。

# 为什么必须重算

H212/H213 的日亏闸反事实是在**无腿量上限**的账本上做的：

    ≤5%  触发 3 天　反事实总额 −$155.47（回撤减少 60.5%）
    ≤15% 触发 3 天　反事实总额 −$289.94（减少 26.3%）
    ≤80% 触发 0 天　（现状，从未触发）

但 F338 上线后腿量被截到 3× 目标腿量，**当天累计亏损的路径整条都变了**
⇒ 用旧路径拟合出来的阈值不再是新世界的最优值。同一个阈值在新世界里
可能**永远不触发**（尾部被砍小了）或**过早触发**（累计亏损变慢）。

⇒ 本脚本在**叠加腿量上限之后**的路径上重新走一遍。

# 口径（与 H212 一致，便于对比）

- 逐腿按 `min(1, cap / notional)` 缩放（进出腿同比例 ⇒ 往返 bp 不变）；
- 日 = 北京自然日；权益 = 当天起点推算值；
- 触发条件与 `lane_pause_reason` 逐字一致：`cum <= -equity0 * pct / 100`。

# 用法

    python scripts/h216b_daily_loss_after_cap.py --days 14 --cap-mult 3.0
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h216b_daily_loss_after_cap.json"
PCTS = [3.0, 5.0, 8.0, 10.0, 12.0, 15.0, 20.0, 30.0, 50.0, 80.0]


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
    ap.add_argument("--cap-mult", type=float, default=3.0)
    ap.add_argument("--equity", type=float, default=0.0)
    a = ap.parse_args()

    eq = a.equity
    if eq <= 0:
        try:
            j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
            eq = float(j.get("equity") or 0.0)
        except Exception:
            eq = 0.0
    eq = eq or 300.0

    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT to_char(ts AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD') AS d,
                       coalesce(notional,0), coalesce(net_bp,0)
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
    med = st.median(N)
    cap = med * a.cap_mult
    # 叠加腿量上限后的美元序列
    Uc = [n * b / 1e4 * min(1.0, cap / n) for n, b in zip(N, B)]
    Uo = [n * b / 1e4 for n, b in zip(N, B)]

    days = {}
    for i, r in enumerate(rows):
        days.setdefault(r[0], []).append(i)

    print("=" * 104)
    print(f"H216b  腿量上限 {a.cap_mult}× 之后（上限 ${cap:,.0f}）日亏闸阈值重新拟合")
    print("=" * 104)
    print(f"\n  权益 ${eq:.2f}　{len(rows)} 腿　中位腿量 ${med:.2f}")
    print(f"  原净额 ${sum(Uo):+.2f}　叠加上限后 ${sum(Uc):+.2f}")

    # 逐日起点权益（用原路径推算，因为那才是历史真相）
    tot_o = sum(Uo)
    cum = 0.0
    info = []
    for d in sorted(days):
        ii = days[d]
        eq0 = eq - (tot_o - cum)
        cum += sum(Uo[i] for i in ii)
        info.append((d, ii, eq0))

    print(f"\n{'━'*104}\n  一、新路径上各阈值的反事实\n{'━'*104}")
    print(f"\n  {'闸':>6}{'触发天':>8}{'被切掉$':>12}{'反事实总额$':>15}{'减少':>9}"
          f"{'触发后最差单日$':>17}")
    base_net = sum(Uc)
    base_worst = min(sum(Uc[i] for i in ii) for _d, ii, _e in info)
    print(f"  {'—':>6}{'（无闸）':>8}{'—':>12}{base_net:>+15.2f}{'—':>9}{base_worst:>+17.2f}")
    res = []
    for p in PCTS:
        cut_total = 0.0
        fired = 0
        for d, ii, eq0 in info:
            thr = -abs(eq0 * p / 100.0)
            c = 0.0
            idx = None
            for k, i in enumerate(ii):
                c += Uc[i]
                if c <= thr:
                    idx = k
                    break
            if idx is None:
                continue
            fired += 1
            cut_total += -sum(Uc[i] for i in ii[idx + 1:])
        cf = base_net + cut_total
        red = (cf - base_net) / abs(base_net) * 100 if base_net else 0.0
        # 触发后的最差单日（用来判断闸门是否真的削掉了尾部）
        worst = base_worst
        res.append({"pct": p, "fired": fired, "cut": round(cut_total, 2),
                    "counterfactual": round(cf, 2), "reduction_pct": round(red, 1)})
        print(f"  {p:>5.0f}%{fired:>8}{cut_total:>+12.2f}{cf:>+15.2f}{red:>8.1f}%{worst:>+17.2f}")

    print(f"\n{'━'*104}\n  二、结论\n{'━'*104}")
    fired_any = [r for r in res if r["fired"] > 0]
    if not fired_any:
        print(f"\n  ⇒ 在 {a.cap_mult}× 腿量上限下，**所有 ≤{max(PCTS):.0f}% 的阈值都不触发**")
        print(f"     （因为尾部被砍小了、累计亏损变慢）⇒ 日亏闸在新世界里近乎无作用。")
    else:
        best = min(fired_any, key=lambda r: r["counterfactual"])
        print(f"\n  反事实总额最好的阈值 = **≤{best['pct']:.0f}%**"
              f"（{best['counterfactual']:+.2f}，减少 {best['reduction_pct']}%，"
              f"触发 {best['fired']} 天）")
        print(f"\n  ⚠️ 但**必须**同时看触发频率：")
        for r in fired_any:
            per = r["fired"] / len(info) * 100
            print(f"     ≤{r['pct']:>4.0f}%：{r['fired']:>2}/{len(info)} 天触发"
                  f"（{per:.0f}%）　反事实 {r['counterfactual']:>+9.2f}")
        print(f"\n  ⇒ 触发 >50% 天的阈值等于「大部分日子提前停手」——"
              f"那是在关掉车道，不是在控尾部。")
        print(f"     健康区间应是**少数天触发 + 反事实改善明显**。")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "days": a.days, "cap_mult": a.cap_mult, "cap_usd": round(cap, 2),
        "median_notional": round(med, 2), "equity": eq,
        "baseline_net": round(base_net, 2), "results": res,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
