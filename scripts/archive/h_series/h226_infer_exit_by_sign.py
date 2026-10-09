# -*- coding: utf-8 -*-
"""H226 用 `price_bp` 的**符号**给历史强平归类（F340 之前的 1,070 条）。

# 为什么可以这样推

`exit_reason` 对 F340 之前的 1,070 条强平失效（键都不存在），
但强平路径本身在 `price_bp` 上留下了**方向指纹**：

    ①′ stop_loss     : 浮亏超阈值才触发 ⇒ 平仓时价格**必然逆向** ⇒ price_bp < 0
    ①″ take_profit   : 浮盈超阈值才触发 ⇒ 平仓时价格**必然顺向** ⇒ price_bp > 0
    ②  timeout_taker : 与价格无关（只看时长）⇒ 两侧都可能有（**本配置已关**）
    ②′ ofi_flatten   : 顺 OFI 方向 ⇒ price_bp 偏正（**本配置已关**，阈值 0）
    孤儿 orphan      : 与价格无关（币被移出宇宙）⇒ 两侧都可能有

**当前配置**：`stop_loss_bp=80`、`take_profit_bp=12`、
`timeout_exit_maker_only=True`、`ofi_flatten_threshold=0`、
`stop_maker_grace_sec=0`。

⇒ 若 price_bp 的分布是**双峰且几乎不重叠**，就说明强平基本只由
   ①′/①″ 两条价格触发路径产生，且可以用 `sign(price_bp)` 高置信度归类。

# 这对「大波动就大亏」的意义

用户的观察若成立，机制应该是：
    大波动 ⇒ 浮盈/浮亏**更快**触及阈值 ⇒ 两条价格路径的触发**频率上升**
    ⇒ 但每次强平要付 `fee 4bp + spread` ⇒ **波动越大，付费次数越多**

⇒ 关键判据：**每次强平的净成本是否随波动上升**，以及
   **强平频率是否随波动上升**（H224 已给出弱证据：逐日四分位 2.0×/2.5×）。

# 用法

    python scripts/h226_infer_exit_by_sign.py --days 14
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h226_infer_exit.json"


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


def q(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, max(0, int(round(p / 100.0 * (len(s) - 1)))))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--neutral-bp", type=float, default=1.0,
                    help="|price_bp| 小于此值视为「方向中性」（归入未知）")
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT coalesce(meta_json->>'exit_path','') AS xp,
                       coalesce(notional,0), coalesce(net_bp,0),
                       coalesce(price_bp,0), coalesce(fee_bp,0),
                       coalesce(spread_bp,0), coalesce(symbol,''), ts,
                       (meta_json->'flatten')::text
                FROM lane_ledger
                WHERE lane_id=%s AND ts >= now() - (%s || ' days')::interval
                  AND meta_json IS NOT NULL AND coalesce(notional,0) > 0
                ORDER BY ts ASC
            """, (LANE, str(int(a.days))))
            raw = cur.fetchall()

    legs = []
    for xp, notl, nbp, pbp, fbp, sbp, sym, ts, flat in raw:
        legs.append({"xp": str(xp), "flat": str(flat).lower() == "true",
                     "notional": float(notl), "net_bp": float(nbp),
                     "price_bp": float(pbp), "fee_bp": float(fbp),
                     "spread_bp": float(sbp), "sym": str(sym), "ts": ts,
                     "usd": float(notl) * float(nbp) / 1e4})
    fl = [x for x in legs if x["flat"]]
    mk = [x for x in legs if not x["flat"]]
    if not fl:
        print("窗口内无 flatten 腿")
        return 1

    print("=" * 104)
    print("H226  用 price_bp 符号给历史强平归类")
    print("=" * 104)
    pb = [x["price_bp"] for x in fl]
    print(f"\n  窗口 {a.days} 天　flush 腿 {len(fl)}　maker 腿 {len(mk)}")
    print(f"  强平 price_bp：P10 {q(pb,10):+.2f}　P25 {q(pb,25):+.2f}　"
          f"P50 {q(pb,50):+.2f}　P75 {q(pb,75):+.2f}　P90 {q(pb,90):+.2f}")

    # ── 一、双峰检验（能否用符号归类）──
    print(f"\n{'━'*104}\n  一、price_bp 的分布：是不是双峰（决定符号归类是否可信）\n{'━'*104}")
    EDGES = [-1e9, -40, -20, -10, -4, -1, 1, 4, 10, 20, 40, 1e9]
    print(f"\n  {'price_bp 区间':>16}{'腿数':>8}{'占比':>8}{'净额$':>11}{'fee':>8}")
    for i in range(len(EDGES) - 1):
        lo, hi = EDGES[i], EDGES[i + 1]
        sel = [x for x in fl if lo <= x["price_bp"] < hi]
        if not sel:
            continue
        lbl = (f"<{hi:g}" if i == 0 else
               (f">{lo:g}" if i == len(EDGES) - 2 else f"{lo:g}~{hi:g}"))
        print(f"  {lbl:>16}{len(sel):>8}{len(sel)/len(fl)*100:>7.1f}%"
              f"{sum(x['usd'] for x in sel):>+11.2f}"
              f"{st.mean([x['fee_bp'] for x in sel]):>+8.2f}")
    mid_cnt = sum(1 for x in fl if abs(x["price_bp"]) < a.neutral_bp)
    print(f"\n  |price_bp| < {a.neutral_bp} 的「方向中性」腿 = {mid_cnt}"
          f"（{mid_cnt/len(fl)*100:.1f}%）")

    # ── 二、按符号归类 ──
    print(f"\n{'━'*104}\n  二、按符号归类（推定出口）\n{'━'*104}")
    tl = [x for x in fl if x["price_bp"] > a.neutral_bp]
    sl = [x for x in fl if x["price_bp"] < -a.neutral_bp]
    nt = [x for x in fl if abs(x["price_bp"]) <= a.neutral_bp]
    print(f"\n  {'推定出口':<26}{'腿数':>7}{'占比':>8}{'净额$':>11}"
          f"{'单腿$':>10}{'均值bp':>10}{'名义$':>12}")
    for lbl, sub in (("①″ take_profit（price>0）", tl),
                     ("①′ stop_loss（price<0）", sl),
                     ("方向中性（≥2 条可能）", nt)):
        if not sub:
            print(f"  {lbl:<26}{0:>7}")
            continue
        nn = sum(x["notional"] for x in sub)
        uu = sum(x["usd"] for x in sub)
        print(f"  {lbl:<26}{len(sub):>7}{len(sub)/len(fl)*100:>7.1f}%{uu:>+11.2f}"
              f"{uu/len(sub):>+10.4f}{st.mean([x['net_bp'] for x in sub]):>+10.2f}"
              f"{nn:>12,.0f}")

    # ── 三、两条路径的成本对比（修法完全不同）──
    print(f"\n{'━'*104}\n  三、两条价格路径的成本结构（修法完全不同）\n{'━'*104}")
    for lbl, sub in (("①″ take_profit", tl), ("①′ stop_loss", sl)):
        if not sub:
            continue
        nn = sum(x["notional"] for x in sub)
        uu = sum(x["usd"] for x in sub)
        print(f"\n  {lbl}：{len(sub)} 腿　净额 ${uu:+.2f}　"
              f"加权 {uu/nn*1e4 if nn else 0:+.3f} bp　单腿 ${uu/len(sub):+.4f}")
        print(f"     成分：fee {st.mean([x['fee_bp'] for x in sub]):+.2f}　"
              f"price {st.mean([x['price_bp'] for x in sub]):+.2f}　"
              f"spread {st.mean([x['spread_bp'] for x in sub]):+.2f}")
        print(f"     ⇒ **每次强平 ${{净额/腿数}}** = "
              f"${uu/len(sub):+.4f}；该路径贡献总净额的 "
              f"{uu/sum(x['usd'] for x in fl)*100 if sum(x['usd'] for x in fl) else 0:.1f}%")

    # ── 四、逐日：两条路径的腿数与成本（波动传导）──
    print(f"\n{'━'*104}\n  四、逐日：两条路径的腿数与单腿成本\n{'━'*104}")
    days = {}
    for x in fl:
        days.setdefault(x["ts"].strftime("%Y-%m-%d"), []).append(x)
    print(f"\n  {'日期':<12}{'TP腿':>7}{'TP净$':>10}{'TP单腿$':>11}"
          f"{'SL腿':>7}{'SL净$':>10}{'SL单腿$':>11}{'合计净$':>11}")
    for d in sorted(days):
        v = days[d]
        t = [x for x in v if x["price_bp"] > a.neutral_bp]
        s = [x for x in v if x["price_bp"] < -a.neutral_bp]
        tu = sum(x["usd"] for x in t)
        su = sum(x["usd"] for x in s)
        print(f"  {d:<12}{len(t):>7}{tu:>+10.2f}"
              f"{(tu/len(t) if t else 0):>+11.4f}"
              f"{len(s):>7}{su:>+10.2f}{(su/len(s) if s else 0):>+11.4f}"
              f"{tu+su:>+11.2f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "flatten_n": len(fl), "maker_n": len(mk),
        "take_profit": {"n": len(tl), "usd": round(sum(x["usd"] for x in tl), 2)},
        "stop_loss": {"n": len(sl), "usd": round(sum(x["usd"] for x in sl), 2)},
        "neutral": {"n": len(nt), "usd": round(sum(x["usd"] for x in nt), 2)},
        "flatten_usd": round(sum(x["usd"] for x in fl), 2),
        "maker_usd": round(sum(x["usd"] for x in mk), 2),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
