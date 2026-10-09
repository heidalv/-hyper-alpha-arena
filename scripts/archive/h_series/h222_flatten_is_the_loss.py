# -*- coding: utf-8 -*-
"""H222 全部亏损在 flatten 腿 —— 这才是「大波动 → 大亏」的机制。

# 我此前一直看错的直接原因

按 `seg_low/seg_high` 是否存在把腿分成两族（14 天，23,790 腿）：

| 类型 | seg | 腿数 | 净额 | 名义 | 加权 bp |
|---|---|---|---|---|---|
| maker   | 有 | 16,242 | −$62.49 | $3,640,723 | −0.172 |
| maker   | 无 |  6,442 |  +$1.93 | $1,025,091 | +0.019 |
| **flatten** | 有 |  586 | **−$169.54** | $117,407 | **−14.44** |
| **flatten** | 无 |  503 | **−$163.97** |  $72,253 | **−22.69** |

⇒ **1,089 条 flatten 腿 = −$333.51，即全部亏损。**
maker 做市合计只有 −$60.56（≈ −0.16 bp/腿，基本打平）。

而 `flatten` 腿的 `seg_low/seg_high` 是**空**的 —— 代码里那个分支
（`plan_orphan_exit` / 强平路径不产生"区间"，因为它是立即被吃的 taker 单），
所以任何"按桶内区间分桶"的分析**结构性地看不到 flatten 腿**。
H220 / H221 因此都得出了"大波动是赚的"这个错误结论。

# 口径修正

- **maker 腿**：用 `|seg_high-seg_low|` 与逆向幅度（成交价 → 区间极值）；
- **flatten 腿**：没有区间 ⇒ 用**成交价相对当时中价的偏离**
  （`|fill_px - mid_px| / mid_px`）作为过价成本，
  并直接看 `fee_bp` / `slippage_bp` 字段。

# 用法

    python scripts/h222_flatten_is_the_loss.py --days 14
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h222_flatten_is_the_loss.json"


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
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT (meta_json->'flatten')::text,
                       coalesce(meta_json->>'seg_low',''),
                       coalesce(meta_json->>'seg_high',''),
                       coalesce(meta_json->>'mid_px',''),
                       coalesce(meta_json->>'fill_px',''),
                       coalesce(meta_json->>'side',''),
                       coalesce(meta_json->>'exit_reason',''),
                       coalesce(meta_json->>'exit_action',''),
                       coalesce(notional,0), coalesce(net_bp,0),
                       coalesce(spread_bp,0), coalesce(price_bp,0),
                       coalesce(fee_bp,0), coalesce(slippage_bp,0),
                       coalesce(symbol,''), ts
                FROM lane_ledger
                WHERE lane_id=%s AND ts >= now() - (%s || ' days')::interval
                  AND meta_json IS NOT NULL AND coalesce(notional,0) > 0
                ORDER BY ts ASC
            """, (LANE, str(int(a.days))))
            raw = cur.fetchall()

    legs = []
    for (flat, lo, hi, mid, fpx, side, xr, xa, notl, nbp, sbp, pbp,
         fbp, slp, sym, ts) in raw:
        is_flat = str(flat).lower() == "true"
        try:
            mid_f = float(mid)
            fp_f = float(fpx)
        except Exception:
            continue
        if mid_f <= 0 or fp_f <= 0:
            continue
        # 过价：成交价相对中价的偏离（对 maker 腿是**赚**的价差，对 flatten 是**付**的成本）
        if side == "buy":
            cross = (mid_f - fp_f) / mid_f * 1e4      # >0 = 买在中价下方（好）
        elif side == "sell":
            cross = (fp_f - mid_f) / mid_f * 1e4      # >0 = 卖在中价上方（好）
        else:
            continue
        seg_ok = False
        try:
            seg_ok = float(lo) > 0 and float(hi) > 0
        except Exception:
            seg_ok = False
        legs.append({
            "flat": is_flat, "seg_ok": seg_ok, "cross_bp": cross,
            "notional": float(notl), "net_bp": float(nbp),
            "spread_bp": float(sbp), "price_bp": float(pbp),
            "fee_bp": float(fbp), "slip_bp": float(slp),
            "usd": float(notl) * float(nbp) / 1e4,
            "xr": str(xr), "xa": str(xa), "sym": str(sym), "ts": ts,
        })

    if not legs:
        print("无数据")
        return 1

    print("=" * 104)
    print("H222  全部亏损来自 flatten 腿")
    print("=" * 104)
    print(f"\n  窗口 {a.days} 天　腿 {len(legs)}")

    # ── 一、四象限总表 ──
    print(f"\n{'━'*104}\n  一、两族 × seg 有无（完整四象限）\n{'━'*104}")
    print(f"\n  {'类型':<10}{'seg':>6}{'腿数':>8}{'名义$':>13}{'净额$':>11}"
          f"{'加权bp':>10}{'中位bp':>9}")
    quad = {}
    for is_flat in (False, True):
        for sk in (True, False):
            sel = [x for x in legs if x["flat"] == is_flat and x["seg_ok"] == sk]
            if not sel:
                continue
            nn = sum(x["notional"] for x in sel)
            uu = sum(x["usd"] for x in sel)
            lbl = "flatten" if is_flat else "maker"
            print(f"  {lbl:<10}{str(sk):>6}{len(sel):>8}{nn:>13,.0f}{uu:>+11.2f}"
                  f"{uu/nn*1e4 if nn else 0:>+10.3f}"
                  f"{st.median([x['net_bp'] for x in sel]):>+9.2f}")
            quad[f"{lbl}_{'seg' if sk else 'noseg'}"] = {
                "n": len(sel), "notional": round(nn, 0), "usd": round(uu, 2),
                "w_bp": round(uu / nn * 1e4, 3) if nn else None}

    mk = [x for x in legs if not x["flat"]]
    fl = [x for x in legs if x["flat"]]
    for lbl, sub in (("maker", mk), ("flatten", fl)):
        nn = sum(x["notional"] for x in sub)
        uu = sum(x["usd"] for x in sub)
        print(f"\n  **{lbl} 合计**：{len(sub)} 腿（{len(sub)/len(legs)*100:.1f}%）　"
              f"名义 ${nn:,.0f}　净额 **${uu:+.2f}**　"
              f"加权 {uu/nn*1e4 if nn else 0:+.3f} bp　"
              f"单腿均值 ${uu/len(sub):+.4f}")

    print(f"\n  ⇒ maker 腿净额 ${sum(x['usd'] for x in mk):+.2f}，"
          f"flatten 腿净额 ${sum(x['usd'] for x in fl):+.2f}")
    print(f"  ⇒ 若**完全不做任何 flatten**（假设仓位神奇消失），总净额会是 "
          f"${sum(x['usd'] for x in mk):+.2f} 而不是 ${sum(x['usd'] for x in legs):+.2f}")

    # ── 二、flatten 的成本结构 ──
    print(f"\n{'━'*104}\n  二、flatten 腿的成本拆解（每条要付多少）\n{'━'*104}")
    if fl:
        nn = sum(x["notional"] for x in fl)
        uu = sum(x["usd"] for x in fl)
        print(f"\n  加权 net_bp = {uu/nn*1e4:+.3f}　"
              f"（maker 腿 = {sum(x['usd'] for x in mk)/sum(x['notional'] for x in mk)*1e4:+.3f}）")
        print(f"  ⇒ **flatten 比 maker 贵 "
              f"{(sum(x['usd'] for x in mk)/sum(x['notional'] for x in mk)*1e4 - uu/nn*1e4):.3f} bp/腿**")
        print(f"\n  成分均值：")
        print(f"    spread_bp  {st.mean([x['spread_bp'] for x in fl]):+9.3f}")
        print(f"    price_bp   {st.mean([x['price_bp'] for x in fl]):+9.3f}")
        print(f"    fee_bp     {st.mean([x['fee_bp'] for x in fl]):+9.3f}")
        print(f"    slip_bp    {st.mean([x['slip_bp'] for x in fl]):+9.3f}")
        print(f"    cross_bp   {st.mean([x['cross_bp'] for x in fl]):+9.3f}"
              f"（成交价相对中价；负 = 过价付出）")
        print(f"\n  名义分位：P50 ${q([x['notional'] for x in fl],50):,.0f}　"
              f"P90 ${q([x['notional'] for x in fl],90):,.0f}　"
              f"max ${max(x['notional'] for x in fl):,.0f}")
        print(f"  单腿亏损分位：P50 ${q([x['usd'] for x in fl],50):+.3f}　"
              f"P10 ${q([x['usd'] for x in fl],10):+.3f}　"
              f"min ${min(x['usd'] for x in fl):+.3f}")

    # ── 三、maker 腿只在"被真打穿"时亏 ──
    print(f"\n{'━'*104}\n  三、maker 腿：按成交价相对中价的过价分桶\n{'━'*104}")
    EDGES = [-1e9, -8, -4, -2, -0.5, 0.5, 2, 4, 8, 1e9]
    print(f"\n  {'cross_bp':>14}{'腿数':>8}{'净额$':>11}{'加权bp':>10}")
    for i in range(len(EDGES) - 1):
        lo, hi = EDGES[i], EDGES[i + 1]
        sel = [x for x in mk if lo <= x["cross_bp"] < hi]
        if not sel:
            continue
        nn = sum(x["notional"] for x in sel)
        uu = sum(x["usd"] for x in sel)
        lbl = (f"{lo:g}~{hi:g}" if i > 0 and i < len(EDGES) - 2
               else (f"<{hi:g}" if i == 0 else f">{lo:g}"))
        print(f"  {lbl:>14}{len(sel):>8}{uu:>+11.2f}{uu/nn*1e4 if nn else 0:>+10.3f}")
    print(f"\n  注：`cross_bp` = 成交价相对中价的偏离（买在中价下方为正）。"
          f"正常做市腿应显著为正。")

    # ── 四、逐日：flatten 的亏损是否每天都发生 ──
    print(f"\n{'━'*104}\n  四、逐日：maker 净额 vs flatten 净额（跨窗口判据）\n{'━'*104}")
    days = {}
    for x in legs:
        days.setdefault(x["ts"].strftime("%Y-%m-%d"), []).append(x)
    print(f"\n  {'日期':<12}{'总腿':>7}{'maker腿':>8}{'maker净$':>11}{'flat腿':>7}"
          f"{'flat净$':>11}{'flat占比':>10}{'总净$':>10}")
    nfl = 0
    nd = 0
    for d in sorted(days):
        v = days[d]
        m = [z for z in v if not z["flat"]]
        f = [z for z in v if z["flat"]]
        mu = sum(z["usd"] for z in m)
        fu = sum(z["usd"] for z in f)
        nd += 1
        if f:
            nfl += 1
        print(f"  {d:<12}{len(v):>7}{len(m):>8}{mu:>+11.2f}{len(f):>7}{fu:>+11.2f}"
              f"{len(f)/len(v)*100 if v else 0:>9.1f}%{mu+fu:>+10.2f}")
    print(f"\n  ⇒ 有 flatten 的天数 = **{nfl}/{nd}**")

    # ── 五、出场原因归因（flatten 腿）──
    print(f"\n{'━'*104}\n  五、flatten 腿的触发出处\n{'━'*104}")
    by = {}
    for x in fl:
        by.setdefault(x["xr"] or "(空)", []).append(x)
    print(f"\n  {'exit_reason':<24}{'腿数':>7}{'净额$':>12}{'加权bp':>10}{'单腿均值$':>12}")
    for k in sorted(by, key=lambda k: sum(z["usd"] for z in by[k])):
        v = by[k]
        nn = sum(z["notional"] for z in v)
        uu = sum(z["usd"] for z in v)
        print(f"  {k:<24}{len(v):>7}{uu:>+12.2f}{uu/nn*1e4 if nn else 0:>+10.3f}"
              f"{uu/len(v):>+12.4f}")

    # ── 六、exit_action 分布 ──
    print(f"\n{'━'*104}\n  六、exit_action（flatten 腿由哪条路径产生）\n{'━'*104}")
    bya = {}
    for x in fl:
        bya.setdefault(x["xa"] or "(空)", []).append(x)
    print(f"\n  {'exit_action':<20}{'腿数':>7}{'净额$':>12}{'加权bp':>10}")
    for k in sorted(bya, key=lambda k: -len(bya[k])):
        v = bya[k]
        nn = sum(z["notional"] for z in v)
        uu = sum(z["usd"] for z in v)
        print(f"  {k:<20}{len(v):>7}{uu:>+12.2f}{uu/nn*1e4 if nn else 0:>+10.3f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "legs": len(legs), "maker_n": len(mk), "flatten_n": len(fl),
        "maker_usd": round(sum(x["usd"] for x in mk), 2),
        "flatten_usd": round(sum(x["usd"] for x in fl), 2),
        "maker_w_bp": round(sum(x["usd"] for x in mk) /
                            sum(x["notional"] for x in mk) * 1e4, 3),
        "flatten_w_bp": (round(sum(x["usd"] for x in fl) /
                               sum(x["notional"] for x in fl) * 1e4, 3) if fl else None),
        "quad": quad, "exit_reason": {k: round(sum(z["usd"] for z in v), 2)
                                      for k, v in by.items()},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
