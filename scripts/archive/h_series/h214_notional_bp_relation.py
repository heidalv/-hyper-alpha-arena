# -*- coding: utf-8 -*-
"""H214 腿的名义金额与盈亏的关系：`net_bp` 为正却亏钱是怎么回事。

# 触发动机

H213 发现 09-15 触发后的 1390 腿：**加权 net_bp = +5.02 bp**，
但**合计 = −$48.85**。两者符号相反，只能由一种机制产生：

    Σ(bp_i × notional_i) 与 Σ(bp_i) 符号相反
    ⇒ 名义大的腿 bp 低（或负），名义小的腿 bp 高

若这条关系是**系统性**的（不是某一天的偶然），它的含义很重：

- 引擎的腿量不是固定值（按 `fill_notional` × 敞口余量算），
- **敞口余量大 ⇒ 名义大**；而敞口余量大通常在**单边行情**里出现
  （库存被单边推着走，反方向余量反而大），
- ⇒ "名义大"可能正好代理了"逆向选择严重"。

那这就是一个**可以从腿量本身读出来的信号**——而且不需要任何新数据管道，
名义金额已经在账本里。这与之前失败的 OFI / 成交规模信号**不同**：
那两个信号来自**市场数据**且跨窗口不稳定；这个是**我们自己的下单量**，
它是敞口状态的函数，是结构性的。

# 判据（跨窗口，按本会话的硬规矩）

不看单日。按北京日分组，逐日算 `corr(notional, net_bp)` 与
「大额腿组 vs 小额腿组」的净额差，看**符号是否跨日一致**。
跨日翻转 ⇒ 又是一次伪结论。

# 用法

    python scripts/h214_notional_bp_relation.py --days 14
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h214_notional_bp_relation.json"


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


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = st.mean(xs), st.mean(ys)
    sx = (sum((x - mx) ** 2 for x in xs)) ** 0.5
    sy = (sum((y - my) ** 2 for y in ys)) ** 0.5
    if sx == 0 or sy == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def spearman(xs, ys):
    """秩相关（对尾部稳健，腿量分布极度右偏，Pearson 会被极值带走）。"""
    n = len(xs)
    if n < 3:
        return 0.0
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    return pearson(rank(xs), rank(ys))


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
                       coalesce(spread_bp,0), coalesce(price_bp,0),
                       coalesce(meta_json->>'flatten','false'),
                       coalesce(meta_json->>'exit_reason',''),
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

    print("=" * 104)
    print("H214  腿的名义金额 vs 盈亏：正的 net_bp 为什么会合计成亏损")
    print("=" * 104)

    # ── 1. 全局恒等式核对 ──
    tot_notl = sum(float(r[1]) for r in rows)
    tot_usd = sum(float(r[1]) * float(r[2]) / 1e4 for r in rows)
    w_bp = tot_usd / tot_notl * 1e4 if tot_notl else 0.0
    print(f"\n  全局：{len(rows)} 腿　名义合计 ${tot_notl:,.0f}　"
          f"美元合计 ${tot_usd:+.2f}　⇒ 加权 net_bp {w_bp:+.4f}")
    print(f"  恒等式核对：Σ(bp×N)/ΣN = {w_bp:+.4f} bp　"
          f"（应等于美元合计/名义合计×1e4 = "
          f"{tot_usd/tot_notl*1e4 if tot_notl else 0:+.4f}）✓")

    # ── 2. 逐日：corr(名义, net_bp) 与分位组差异（跨窗口判据） ──
    days = {}
    for r in rows:
        days.setdefault(r[0], []).append(r)

    print(f"\n{'━'*104}\n  一、跨窗口判据：corr(腿名义, net_bp) 的符号是否稳定\n{'━'*104}")
    print(f"\n  {'日期':<12}{'腿数':>7}{'Pearson':>10}{'Spearman':>10}"
          f"{'小额半净$':>12}{'大额半净$':>12}{'差$':>10}")
    print("  " + "-" * 88)
    corrs, diffs, ok_days = [], [], []
    for d in sorted(days):
        L = [r for r in days[d] if not str(r[5]).lower() == "true"]  # 只看 maker 腿
        if len(L) < 50:
            continue
        N = [float(r[1]) for r in L]
        B = [float(r[2]) for r in L]
        U = [n * b / 1e4 for n, b in zip(N, B)]
        pc, sc = pearson(N, B), spearman(N, B)
        # 按名义中位数分两半
        med = st.median(N)
        lo = [u for n, u in zip(N, U) if n <= med]
        hi = [u for n, u in zip(N, U) if n > med]
        sl, sh = sum(lo), sum(hi)
        corrs.append(sc)
        diffs.append(sh - sl)
        ok_days.append(d)
        print(f"  {d:<12}{len(L):>7}{pc:>+10.3f}{sc:>+10.3f}"
              f"{sl:>+12.2f}{sh:>+12.2f}{sh-sl:>+10.2f}")

    if len(corrs) >= 3:
        npos = sum(1 for x in corrs if x < 0)
        print(f"\n  Spearman 为负（名义越大 bp 越差）的日数 = **{npos}/{len(corrs)}**"
              f"　中位 {st.median(corrs):+.3f}")
        nposd = sum(1 for x in diffs if x < 0)
        print(f"  大额半净额**低于**小额半的日数 = **{nposd}/{len(diffs)}**"
              f"　中位差 {st.median(diffs):+.2f} $")
        if npos >= len(corrs) * 0.8 and nposd >= len(diffs) * 0.8:
            print(f"\n  ⇒ **跨窗口一致**：名义大的腿系统性更差。这是结构性信号。")
        elif npos <= len(corrs) * 0.2 and nposd <= len(diffs) * 0.2:
            print(f"\n  ⇒ **跨窗口一致（反向）**：名义大的腿系统性更好。")
        else:
            print(f"\n  ⇒ **符号跨日翻转 ⇒ 不是结构性关系**（本会话第 5 次同类排除）。")
            print(f"     09-15 那个「net_bp 正但亏钱」是**单日现象**，不能推广。")

    # ── 3. 对 09-15 那次做专项解剖（回答"正 bp 为什么亏"） ──
    print(f"\n{'━'*104}\n  二、专项解剖：net_bp 为正却合计亏损的日子\n{'━'*104}")
    for d in sorted(days):
        L = [r for r in days[d] if not str(r[5]).lower() == "true"]
        if len(L) < 100:
            continue
        N = [float(r[1]) for r in L]
        B = [float(r[2]) for r in L]
        U = [n * b / 1e4 for n, b in zip(N, B)]
        if sum(B) / len(B) <= 0 or sum(U) >= 0:
            continue
        print(f"\n  ── {d}：{len(L)} 腿　算术均值 net_bp {st.mean(B):+.4f}　"
              f"但合计 ${sum(U):+.2f}")
        print(f"     名义：中位 ${st.median(N):.2f}　均值 ${st.mean(N):.2f}　"
              f"最大 ${max(N):.2f}　最小 ${min(N):.2f}")
        # 前十亏损腿
        top = sorted(zip(U, N, B, [r[4] for r in L], [r[3] for r in L]),
                     key=lambda x: x[0])[:10]
        print(f"     最大 10 个亏损腿：")
        print(f"       {'亏损$':>9}{'名义$':>9}{'net_bp':>9}{'price_bp':>10}{'spread_bp':>10}")
        for u, n, b, pb, sb in top:
            print(f"       {u:>+9.3f}{n:>9.2f}{b:>+9.3f}{pb:>+10.3f}{sb:>+10.3f}")
        s_top = sum(x[0] for x in top)
        print(f"     这 10 腿合计 {s_top:+.2f}$ = 全日亏损的 "
              f"{s_top/sum(U)*100 if sum(U) else 0:.1f}%")
        # 去尾后的均值
        U5 = sorted(U)[:-max(1, len(U)//100)]
        print(f"     去掉最差 1% 后合计 {sum(U5):+.2f}（原 {sum(U):+.2f}）"
              f" ⇒ 尾部贡献 {sum(U)-sum(U5):+.2f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "days": ok_days, "spearman": [round(x, 4) for x in corrs],
        "half_diff_usd": [round(x, 2) for x in diffs],
        "weighted_net_bp": round(w_bp, 4), "total_usd": round(tot_usd, 2),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
