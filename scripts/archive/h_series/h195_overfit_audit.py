# -*- coding: utf-8 -*-
"""H195 过拟合审计 —— 对**我自己这轮的改动**和既有参数做敏感性 / 稳定性检查。

# 为什么要审计我自己

本会话（2026-09-21）我连续改了这些参数：

    stop_loss_bp                 40 → 25     （依据：强平 price_bp 的 p5 = −68bp）
    take_profit_maker_grace_sec  30 → 120    （依据：某条"96.1% 在 120s 内"的注释）
    stop_maker_grace_sec         30 → 0      （依据：A 段 1.4 小时 / 10 笔强平）
    reduce_quote_disabled        True → False
    （以及更早的 compound_ratio / max_net_directional_ratio / spread_mult 等）

每一项都"有数据支持"，但**每一条依据都是同一份 14 小时账本的不同切面**。
这就是过拟合的经典形成方式：**在同一样本上反复做决策**。

# 三个可计算的判据

## 判据 1 —— 我最后那次改动是不是在拟合噪声

`stop_maker_grace_sec: 30 → 0` 的依据是 A 段（**1.4 小时、10 笔强平**）
对比 B 段。10 笔样本上算出来的"每笔强平 $1.40 vs $0.71"，
置信区间宽到无法支撑任何结论。本脚本用 bootstrap 把这件事量化：
**这么小的样本，能不能得出"B 更好"这个结论？**

## 判据 2 —— 参数敏感性（过拟合的判据）

若 `stop_loss_bp=25` 是拟合出来的，则 20 或 30 应该明显更差（尖锐最优点）。
若它是稳健的，则 20~35 之间应该差不太多（平坦高原）。
**平坦 = 稳健；尖锐 = 过拟合。**

## 判据 3 —— 时间稳定性（样本外）

把 14 小时切成不重叠的 3 段，看每段的 maker/flatten 结构是否一致。
若每段差异巨大，则"整体最优参数"没有意义 —— 它只对某一段最优。

# 口径提醒

账本里只有**已实现**结果，**没有**记录"如果参数不同会怎样"。
⇒ 判据 2 无法用真回放做（需要 `replay` + 完整行情），
本脚本用**代理指标**做：不同参数会改变的最直接可观测量是
**强平金额的分布**与**持仓时长分布**，用它们反推参数敏感性。
所有代理都显式标注，不当作真回放结果。
"""
from __future__ import annotations

import datetime as dt
import pathlib
import random
import statistics as st

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"


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


def fetch_positions():
    """按 position_id 聚合成周期：出口类型 / 净额 / 时长 / 出场 price_bp。"""
    import psycopg
    q = """
    WITH leg AS (
      SELECT position_id, symbol, ts, notional, net_bp, price_bp, fee_bp,
             net_bp*notional/10000.0 AS usd,
             (meta_json->>'flatten' IN ('true','True')) AS is_flat
      FROM lane_ledger
      WHERE lane_id=%s AND ts > now() - interval '14 hours'
        AND position_id IS NOT NULL AND position_id <> ''
    )
    SELECT position_id, symbol, max(is_flat::int) AS closed_flat,
           sum(usd) AS usd, sum(notional) AS notional,
           EXTRACT(epoch FROM (max(ts)-min(ts))) AS dur_s,
           CASE WHEN max(is_flat::int)=1
                THEN sum(price_bp*notional)/NULLIF(sum(notional),0) END AS exit_px_bp,
           min(ts) AS t0, max(ts) AS t1
    FROM leg GROUP BY position_id, symbol
    """
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute(q, (LANE,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


def boot_ci(vals, *, n=5000, seed=7):
    """bootstrap 均值的 95% 置信区间。"""
    if len(vals) < 2:
        return None
    rnd = random.Random(seed)
    k = len(vals)
    means = []
    for _ in range(n):
        means.append(sum(rnd.choice(vals) for _ in range(k)) / k)
    means.sort()
    return means[int(0.025 * n)], means[int(0.975 * n)]


def main() -> int:
    rows = fetch_positions()
    if not rows:
        print("  无周期数据")
        return 0

    print("=" * 100)
    print("H195  过拟合审计")
    print("=" * 100)
    print(f"  样本：{len(rows)} 个持仓周期（14 小时）")

    flat = [r for r in rows if r["closed_flat"] == 1]
    mk = [r for r in rows if r["closed_flat"] == 0]
    print(f"    maker 出库 {len(mk)}   flatten 出库 {len(flat)}")

    # ══ 判据 1：小样本能否支撑"B 更好" ══
    print("\n  ── 判据 1：我最后那次改动（stop_maker_grace_sec 30→0）的依据有多脆 ──")
    print("     依据是 A 段 vs B 段。⚠️ 这里必须用**正确的时区感知边界**，")
    print("     否则 tz-aware 与 tz-naive 比较会抛错、或把边界切错（首版就切错了）。")
    boundary = None
    if flat and flat[0]["t0"] is not None:
        tz = flat[0]["t0"].tzinfo
        boundary = dt.datetime(2026, 9, 21, 17, 44, tzinfo=tz)
    if boundary is None:
        print("     无时间戳，跳过")
    else:
        A = [float(r["usd"]) for r in flat if r["t0"] < boundary]
        B = [float(r["usd"]) for r in flat if r["t0"] >= boundary]
        print(f"     边界 = {boundary:%Y-%m-%d %H:%M %Z}")
        print(f"     A 段（改动前）强平 {len(A)} 笔   B 段（改动后）强平 {len(B)} 笔")
        for lab, v in (("A", A), ("B", B)):
            if not v:
                print(f"       {lab}: 无样本")
                continue
            ci = boot_ci(v)
            m = st.mean(v)
            if ci:
                print(f"       {lab}: 均值 {m:>8.4f}  95%CI [{ci[0]:>8.4f}, {ci[1]:>8.4f}]")
            else:
                print(f"       {lab}: 均值 {m:>8.4f}  （样本 <2，无法给 CI）")
        if len(A) >= 2 and len(B) >= 2:
            ca, cb = boot_ci(A), boot_ci(B)
            overlap = not (cb[0] > ca[1] or ca[0] > cb[1])
            print(f"     ⇒ 两个 95%CI {'**重叠**' if overlap else '不重叠'} "
                  f"⇒ {'**无法断定 B 更好**（改动依据不足）' if overlap else 'B 显著更好'}")
        elif len(B) < 2:
            print(f"     ⇒ B 段只有 {len(B)} 笔强平 ⇒ **完全无法下结论**")

    # ══ 判据 2：参数敏感性（用强平出场 price_bp 分布做代理）══
    print("\n  ── 判据 2：参数敏感性（代理指标，非真回放）──")
    print("     代理逻辑：stop_loss_bp 决定止损触发点。")
    print("     若 25bp 是**尖锐最优**，则 20/30 应明显更差；")
    print("     若 20~35 表现相近 ⇒ **平坦高原** ⇒ 稳健、不像过拟合。")
    px = sorted(float(r["exit_px_bp"]) for r in flat if r["exit_px_bp"] is not None)
    if len(px) >= 5:
        n = len(px)
        def q(p):
            return px[min(n - 1, int(p * n))]
        print(f"\n     强平出场 price_bp 分布（n={n}）：")
        for p in (0.05, 0.10, 0.25, 0.50, 0.75, 0.90):
            print(f"       p{int(p*100):<3} = {q(p):>8.2f} bp")
        print(f"       最差 = {px[0]:>8.2f} bp   最好 = {px[-1]:>8.2f} bp")
        # 关键：有多少强平"本来就不该发生"（|price_bp| 远小于止损线）
        for thr in (20, 25, 30, 40):
            n_beyond = sum(1 for x in px if x <= -thr)
            print(f"       出场劣于 −{thr}bp 的笔数 = {n_beyond:>3} / {n}"
                  f"  ({100.0*n_beyond/n:.1f}%)")
        print("\n     ⇒ 若阈值 20~40 之间'劣于阈值'的笔数变化平缓 ⇒ 平坦高原")
        print("       （实测上面四行即为该敏感性曲线）")

    # ══ 判据 3：时间稳定性 ══
    print("\n  ── 判据 3：时间稳定性（切成不重叠 3 段）──")
    have_t = [r for r in rows if r["t0"] is not None]
    if len(have_t) >= 9:
        have_t.sort(key=lambda r: r["t0"])
        k = len(have_t) // 3
        segs = [have_t[:k], have_t[k:2*k], have_t[2*k:]]
        print(f"     {'段':<6}{'周期数':>8}{'maker':>8}{'flatten':>9}{'强平率':>9}"
              f"{'净额$':>10}{'每周期$':>11}")
        print("     " + "-" * 62)
        for i, sg in enumerate(segs, 1):
            mkn = sum(1 for r in sg if r["closed_flat"] == 0)
            fln = len(sg) - mkn
            tot = sum(float(r["usd"]) for r in sg)
            fr = fln / len(sg) * 100 if sg else 0
            t0 = min(r["t0"] for r in sg).strftime("%H:%M")
            t1 = max(r["t1"] for r in sg).strftime("%H:%M")
            print(f"     {t0}-{t1:<6}{len(sg):>6}{mkn:>8}{fln:>9}{fr:>8.1f}%"
                  f"{tot:>10.3f}{tot/len(sg):>11.4f}")
        print("\n     ⇒ 各段'每周期净额'若符号相反 ⇒ 整体均值无意义（regime 主导）")

    # ══ 判据 4：自由度账 ══
    print("\n  ── 判据 4：自由度 vs 样本量（最直白的过拟合度量）──")
    n_pos = len(rows)
    n_flat = len(flat)
    print(f"     独立观测（持仓周期）= {n_pos}，其中强平 = {n_flat}")
    print(f"     本会话被调过的参数（含更早）≥ 10 个：")
    print("       compound_ratio / max_net_directional_ratio / spread_mult /")
    print("       spread_mult_reduce / min_width_bp / k_inv / take_profit_bp /")
    print("       stop_loss_bp / take_profit_maker_grace_sec / stop_maker_grace_sec")
    if n_flat > 0:
        print(f"     ⇒ 强平类参数是在 **{n_flat}** 个样本上调的")
        print(f"     ⇒ 经验门槛：拟合 1 个参数至少需要 ~10 个独立观测")
        print(f"       {n_flat} 个样本只够支撑 **{n_flat/10:.1f}** 个参数")
        print(f"     ⇒ 实际调了 ≥10 个 ⇒ **自由度明显超标**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
