# -*- coding: utf-8 -*-
"""H228 只看当前时代（账户重置之后）的强平成本。

# 为什么必须裁到"重置后"

H226 的 14 天口径被**两重时代污染**：

  ① `compound_ratio` 历史值 3.0（h127）→ 2.0（h185）→ 1.0（h156/现行）
     ⇒ 旧时代的腿量是现在的 **2~3 倍**，强平的美元金额不可比；
  ② `stats_since = 2026-09-22 10:22:37`（账户重置）之前，配置族完全不同。

⇒ 拿 14 天均值解释"现在为什么亏"是本会话反复犯的同一类错误。

# 本脚本回答

在**当前时代 + 当前配置**下：
  · flatten 腿的腿数 / 频率 / 单腿成本 / 成分
  · 按 `price_bp` 符号归类的两条价格路径（止盈 vs 止损）
  · 逐小时看稳定性（样本少时**必须**标注，不能装作 n 很大）

# 用法

    python scripts/h228_current_era_flatten.py
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h228_current_era_flatten.json"


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
    import argparse as _ap
    ap = _ap.ArgumentParser()
    ap.add_argument("--since", default="", help="ISO 时间；默认读注册表 stats_since")
    ap.add_argument("--neutral-bp", type=float, default=1.0)
    a = ap.parse_args()

    since = a.since
    if not since:
        import psycopg as _p
        with _p.connect(dsn()) as c:
            with c.cursor() as cur:
                cur.execute("SELECT meta_json->>'stats_since' FROM lane_registry"
                            " WHERE lane_id=%s", (LANE,))
                since = (cur.fetchone() or [""])[0] or ""
    if not since:
        print("读不到 stats_since ⇒ 用 --since 显式指定")
        return 1

    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT coalesce(meta_json->>'exit_path','') AS xp,
                       (meta_json->'flatten')::text,
                       coalesce(notional,0), coalesce(net_bp,0),
                       coalesce(price_bp,0), coalesce(fee_bp,0),
                       coalesce(spread_bp,0), coalesce(symbol,''), ts
                FROM lane_ledger
                WHERE lane_id=%s AND ts >= %s::timestamptz
                  AND meta_json IS NOT NULL AND coalesce(notional,0) > 0
                ORDER BY ts ASC
            """, (LANE, since))
            raw = cur.fetchall()

    legs = [{"xp": str(r[0]), "flat": str(r[1]).lower() == "true",
             "notional": float(r[2]), "net_bp": float(r[3]),
             "price_bp": float(r[4]), "fee_bp": float(r[5]),
             "spread_bp": float(r[6]), "sym": str(r[7]), "ts": r[8],
             "usd": float(r[2]) * float(r[3]) / 1e4} for r in raw]
    if not legs:
        print("时代内无腿")
        return 1

    fl = [x for x in legs if x["flat"]]
    mk = [x for x in legs if not x["flat"]]
    span_h = (max(x["ts"] for x in legs) - min(x["ts"] for x in legs)).total_seconds() / 3600.0

    print("=" * 100)
    print("H228  当前时代的强平成本")
    print("=" * 100)
    print(f"\n  时代起点 {since}")
    print(f"  跨度 {span_h:.2f} 小时　腿 {len(legs)}"
          f"（maker {len(mk)} / flatten {len(fl)}）")

    def blk(lbl, sub):
        if not sub:
            print(f"\n  {lbl}：0 腿")
            return None
        nn = sum(x["notional"] for x in sub)
        uu = sum(x["usd"] for x in sub)
        print(f"\n  **{lbl}**：{len(sub)} 腿（{len(sub)/len(legs)*100:.2f}%）　"
              f"名义 ${nn:,.0f}　净额 **${uu:+.2f}**　"
              f"加权 {uu/nn*1e4 if nn else 0:+.3f} bp　单腿 ${uu/len(sub):+.4f}")
        print(f"     成分：fee {st.mean([x['fee_bp'] for x in sub]):+.2f}　"
              f"price {st.mean([x['price_bp'] for x in sub]):+.2f}　"
              f"spread {st.mean([x['spread_bp'] for x in sub]):+.2f}")
        if fl and span_h > 0:
            print(f"     频率：{len(sub)/span_h:.1f} 腿/小时")
        return uu

    u_mk = blk("maker", mk)
    u_fl = blk("flatten", fl)
    if u_mk is not None and u_fl is not None:
        tot = u_mk + u_fl
        print(f"\n  ⇒ 合计 ${tot:+.2f}　"
              f"其中 flatten 占亏损的 "
              f"{abs(u_fl)/(abs(u_mk)+abs(u_fl))*100:.1f}%")

    # ── 按出口路径（F340 之后的才有值）──
    print(f"\n{'━'*100}\n  一、按 exit_path（F340 之后才有值）\n{'━'*100}")
    by = {}
    for x in fl:
        by.setdefault(x["xp"] or "(无/空 = F340 之前或未命名)", []).append(x)
    for k in sorted(by, key=lambda k: -len(by[k])):
        v = by[k]
        nn = sum(z["notional"] for z in v)
        uu = sum(z["usd"] for z in v)
        print(f"  {k:<34}{len(v):>5} 腿　净额 ${uu:>+8.2f}　"
              f"单腿 ${uu/len(v):>+7.4f}　均价 {st.mean([z['price_bp'] for z in v]):>+8.2f} bp")

    # ── 按 price_bp 符号归类（F340 之前的行也能用）──
    print(f"\n{'━'*100}\n  二、按 price_bp 符号归类（止盈 vs 止损）\n{'━'*100}")
    tl = [x for x in fl if x["price_bp"] > a.neutral_bp]
    sl = [x for x in fl if x["price_bp"] < -a.neutral_bp]
    nt = [x for x in fl if abs(x["price_bp"]) <= a.neutral_bp]
    print(f"\n  {'推定出口':<30}{'腿数':>6}{'净额$':>10}{'单腿$':>10}"
          f"{'均值price':>11}{'fee':>8}")
    for lbl, sub in (("①″ take_profit (price>0)", tl),
                     ("①′ stop_loss   (price<0)", sl),
                     ("方向中性", nt)):
        if not sub:
            print(f"  {lbl:<30}{0:>6}")
            continue
        uu = sum(x["usd"] for x in sub)
        print(f"  {lbl:<30}{len(sub):>6}{uu:>+10.2f}{uu/len(sub):>+10.4f}"
              f"{st.mean([x['price_bp'] for x in sub]):>+11.2f}"
              f"{st.mean([x['fee_bp'] for x in sub]):>+8.2f}")

    # ── 逐小时 ──
    if fl:
        print(f"\n{'━'*100}\n  三、逐小时（样本少，n 已标注）\n{'━'*100}")
        hrs = {}
        for x in fl:
            hrs.setdefault(x["ts"].strftime("%H:00"), []).append(x)
        print(f"\n  {'小时':<8}{'flush腿':>8}{'净额$':>10}{'单腿$':>10}"
              f"{'TP腿':>6}{'SL腿':>6}")
        for h in sorted(hrs):
            v = hrs[h]
            uu = sum(z["usd"] for z in v)
            nt_ = sum(1 for z in v if z["price_bp"] > a.neutral_bp)
            ns_ = sum(1 for z in v if z["price_bp"] < -a.neutral_bp)
            print(f"  {h:<8}{len(v):>8}{uu:>+10.2f}{uu/len(v):>+10.4f}"
                  f"{nt_:>6}{ns_:>6}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "since": since, "span_hours": round(span_h, 3),
        "legs": len(legs), "maker_n": len(mk), "flatten_n": len(fl),
        "maker_usd": round(u_mk or 0.0, 2), "flatten_usd": round(u_fl or 0.0, 2),
        "take_profit": {"n": len(tl), "usd": round(sum(x["usd"] for x in tl), 2)},
        "stop_loss": {"n": len(sl), "usd": round(sum(x["usd"] for x in sl), 2)},
        "by_exit_path": {k: {"n": len(v), "usd": round(sum(z["usd"] for z in v), 2)}
                         for k, v in by.items()},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
