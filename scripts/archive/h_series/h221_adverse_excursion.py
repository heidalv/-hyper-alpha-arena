# -*- coding: utf-8 -*-
"""H221 用「逆向幅度」而不是「桶内宽度」分解 —— 并修掉 flatten 解析。

# H220 的结果与用户观察相反，所以先怀疑口径

H220 按**桶内波幅**（`|seg_high-seg_low|/mid`）分桶，得到：

    0–3bp    7037 腿   −$33.83   −0.240 bp
    15–25bp   290 腿   +$2.10    +0.271 bp     ← 大波动是**赚**的
    40+bp      43 腿   +$0.88    +0.855 bp

而用户说「遇到大波动行情，直接就大亏」。⇒ 三种可能：

  (a) 用户的「大波动」不是「单桶宽」，而是**持续的方向性行程**（行情趋势）；
  (b) 我的 `flatten` 解析坏了（`flatten` 在 meta 里是**真 JSON 布尔**，
      而 `meta_json->>'flatten'` 对布尔返回 `'true'/'false'` 才对吧？——
      实测返回 **NULL** ⇒ 我此前"无 flatten 腿"的结论是**假的**）；
  (c) 引擎的仓位跨越多桶，**盈亏由整段行程决定，不由单桶决定**。

本脚本同时处理 (a)(b)(c)。

# 关键口径：逆向幅度 ≠ 波幅

    波幅   = (seg_high − seg_low) / mid        ← 我们赚多少「宽度」
    逆向幅度 = 成交后价格**朝对我们不利的方向**走了多少

对我们不利的方向取决于我们是买还是卖：

    我们买（side=buy） ⇒ 不利 = 价格继续**跌** ⇒ 逆向 = (fill_px − seg_low)/fill_px
    我们卖（side=sell）⇒ 不利 = 价格继续**涨** ⇒ 逆向 = (seg_high − fill_px)/fill_px

**这才是"被行情打穿"的正确度量**：桶再宽，只要我们买在最低点就没事；
桶很窄，但我们买在最高点、随后跌到桶底，就是被打穿。

# 修 (b)：flatten 的正确读法

    meta_json->>'flatten'        → 对 JSON 布尔**返回 NULL**（本次踩到的坑）
    meta_json->'flatten'         → 返回 jsonb 布尔
    (meta_json->>'flatten')::bool → 报错
    正确：(meta_json->'flatten')::text IN ('true','false')
    或    coalesce((meta_json->>'flatten'),'false') —— **不成立**

⇒ 用 `meta_json @> '{"flatten": true}'` 或 `(meta_json->'flatten')::text = 'true'`。

# 用法

    python scripts/h221_adverse_excursion.py --days 14
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h221_adverse_excursion.json"


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
            # ⚠️ flatten 的正确读法：(meta_json->'flatten')::text
            cur.execute("""
                SELECT coalesce(meta_json->>'seg_low',''),
                       coalesce(meta_json->>'seg_high',''),
                       coalesce(meta_json->>'mid_px',''),
                       coalesce(meta_json->>'fill_px',''),
                       coalesce(meta_json->>'side',''),
                       coalesce((meta_json->'flatten')::text,'false'),
                       coalesce(meta_json->>'exit_reason',''),
                       coalesce(notional,0), coalesce(net_bp,0),
                       coalesce(spread_bp,0), coalesce(price_bp,0),
                       coalesce(fee_bp,0), coalesce(symbol,''), ts
                FROM lane_ledger
                WHERE lane_id=%s AND ts >= now() - (%s || ' days')::interval
                  AND coalesce(notional,0) > 0 AND meta_json IS NOT NULL
                ORDER BY ts ASC
            """, (LANE, str(int(a.days))))
            raw = cur.fetchall()

    legs = []
    for (lo, hi, mid, fpx, side, flat, xr, notl, nbp, sbp, pbp, fbp, sym, ts) in raw:
        try:
            lo_f, hi_f, mid_f, fp_f = float(lo), float(hi), float(mid), float(fpx)
        except Exception:
            continue
        if min(lo_f, hi_f, mid_f, fp_f) <= 0:
            continue
        # 逆向幅度：成交之后，价格朝我们不利方向走了多少（相对成交价）
        if side == "buy":
            adv = max(0.0, (fp_f - lo_f)) / fp_f * 1e4
        elif side == "sell":
            adv = max(0.0, (hi_f - fp_f)) / fp_f * 1e4
        else:
            continue
        legs.append({
            "adv_bp": adv, "w_bp": abs(hi_f - lo_f) / mid_f * 1e4,
            "notional": float(notl), "net_bp": float(nbp),
            "spread_bp": float(sbp), "price_bp": float(pbp),
            "fee_bp": float(fbp),
            "usd": float(notl) * float(nbp) / 1e4,
            "flat": str(flat).lower() == "true",
            "side": side, "xr": str(xr), "sym": str(sym), "ts": ts,
        })

    if not legs:
        print("无可用腿")
        return 1

    print("=" * 104)
    print("H221  逆向幅度（被打穿多深）而不是桶内宽度")
    print("=" * 104)
    nflat = sum(1 for x in legs if x["flat"])
    print(f"\n  窗口 {a.days} 天　腿 {len(legs)}")
    print(f"  **flatten 腿 = {nflat}**（H220 报 0 是解析 bug："
          f"`->>'flatten'` 对 JSON 布尔返回 NULL）")
    adv = [x["adv_bp"] for x in legs]
    print(f"  逆向幅度 bp：P50 {q(adv,50):.1f}　P75 {q(adv,75):.1f}　"
          f"P90 {q(adv,90):.1f}　P99 {q(adv,99):.1f}　max {max(adv):.1f}")

    # ── 一、按逆向幅度分桶 ──
    print(f"\n{'━'*104}\n  一、按「被打穿多深」分桶\n{'━'*104}")
    EDGES = [0, 1, 2, 4, 7, 12, 20, 35, 1e9]
    print(f"\n  {'逆向bp':>12}{'腿数':>7}{'占比':>7}{'名义$':>12}{'净额$':>11}"
          f"{'加权bp':>10}{'spread':>9}{'price':>9}")
    bands = []
    for i in range(len(EDGES) - 1):
        lo, hi = EDGES[i], EDGES[i + 1]
        sel = [x for x in legs if lo <= x["adv_bp"] < hi]
        if not sel:
            continue
        nn = sum(x["notional"] for x in sel)
        uu = sum(x["usd"] for x in sel)
        lbl = f"{lo:g}–{hi:g}" if hi < 1e9 else f"{lo:g}+"
        w = uu / nn * 1e4 if nn else 0
        print(f"  {lbl:>12}{len(sel):>7}{len(sel)/len(legs)*100:>6.1f}%{nn:>12,.0f}"
              f"{uu:>+11.2f}{w:>+10.3f}"
              f"{st.mean([x['spread_bp'] for x in sel]):>+9.3f}"
              f"{st.mean([x['price_bp'] for x in sel]):>+9.3f}")
        bands.append({"band": lbl, "n": len(sel), "usd": round(uu, 2),
                      "w_bp": round(w, 3)})

    # ── 二、极端被打穿（≥12bp）的腿：换成钱是多少 ──
    print(f"\n{'━'*104}\n  二、严重被打穿（逆向 ≥12bp）的腿\n{'━'*104}")
    for thr in (7, 12, 20, 35):
        sel = [x for x in legs if x["adv_bp"] >= thr]
        if not sel:
            print(f"\n  ≥{thr}bp：0 腿")
            continue
        nn = sum(x["notional"] for x in sel)
        uu = sum(x["usd"] for x in sel)
        print(f"\n  ≥{thr}bp：{len(sel)} 腿（{len(sel)/len(legs)*100:.2f}%）　"
              f"名义 ${nn:,.0f}　净额 **${uu:+.2f}**　"
              f"加权 {uu/nn*1e4 if nn else 0:+.3f} bp")
        print(f"     成分：spread {st.mean([x['spread_bp'] for x in sel]):+.3f}　"
              f"price {st.mean([x['price_bp'] for x in sel]):+.3f}　"
              f"中位名义 ${st.median([x['notional'] for x in sel]):,.0f}　"
              f"最大 ${max(x['notional'] for x in sel):,.0f}")

    # ── 三、flatten 腿（现在解析对了）──
    print(f"\n{'━'*104}\n  三、flatten 腿的真实成本（修正 H220 的解析 bug 后）\n{'━'*104}")
    fl = [x for x in legs if x["flat"]]
    mk = [x for x in legs if not x["flat"]]
    for lbl, sub in (("maker", mk), ("flatten", fl)):
        if not sub:
            print(f"\n  {lbl}：0 腿")
            continue
        nn = sum(x["notional"] for x in sub)
        uu = sum(x["usd"] for x in sub)
        print(f"\n  {lbl}：{len(sub)} 腿　名义 ${nn:,.0f}　净额 **${uu:+.2f}**　"
              f"加权 {uu/nn*1e4 if nn else 0:+.3f} bp")
        print(f"     逆向幅度中位 {st.median([x['adv_bp'] for x in sub]):.1f} bp　"
              f"spread {st.mean([x['spread_bp'] for x in sub]):+.3f}　"
              f"price {st.mean([x['price_bp'] for x in sub]):+.3f}　"
              f"fee {st.mean([x['fee_bp'] for x in sub]):+.3f}")

    # ── 四、逐日：严重被打穿的净额（跨窗口判据）──
    print(f"\n{'━'*104}\n  四、逐日：逆向 ≥7bp 的腿贡献了多少亏损\n{'━'*104}")
    days = {}
    for x in legs:
        days.setdefault(x["ts"].strftime("%Y-%m-%d"), []).append(x)
    print(f"\n  {'日期':<12}{'总腿':>7}{'总净额$':>11}{'≥7bp腿':>8}{'≥7bp净额$':>12}"
          f"{'占比':>8}{'≥7bp加权bp':>13}")
    neg = 0
    nd = 0
    for d in sorted(days):
        v = days[d]
        hh = [z for z in v if z["adv_bp"] >= 7]
        tu = sum(z["usd"] for z in v)
        hu = sum(z["usd"] for z in hh)
        hn = sum(z["notional"] for z in hh)
        nd += 1
        if hu < 0:
            neg += 1
        print(f"  {d:<12}{len(v):>7}{tu:>+11.2f}{len(hh):>8}{hu:>+12.2f}"
              f"{len(hh)/len(v)*100 if v else 0:>7.1f}%"
              f"{hu/hn*1e4 if hn else 0:>+13.3f}")
    print(f"\n  ⇒ 该组净额为负的天数 = **{neg}/{nd}**")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "legs": len(legs), "flatten_n": nflat, "bands": bands,
        "adv_p50": round(q(adv, 50), 2), "adv_p99": round(q(adv, 99), 2),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
