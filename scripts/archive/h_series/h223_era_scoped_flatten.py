# -*- coding: utf-8 -*-
"""H223 时代裁剪：只算当前 4 币宇宙的 flatten 成本。

# 为什么必须裁时代

H222 发现 flatten 腿 = −$333.50（全部亏损），但币种分布横跨 **22 个币**，
含 ETH / BTC / DOGE / ONDO / ARB / UNI / ZEC / PENDLE / VIRTUAL / XMR 等
**早已不在宇宙里**的币。逐日 flatten 占比也分成两簇：

    09-09 ~ 09-13   35% ~ 50%   ← 旧宇宙（多币，正在被换掉）
    09-14 ~ 09-22    1.5% ~ 4%   ← 现宇宙

⇒ 用 14 天平均会把"旧宇宙遗留仓位被强平"的成本摊到当前策略头上，
**这正是本会话反复出现的错误类型（拿旧配置的账算新配置）**。

# 三种口径并列（都出，不挑好看的）

  A. 全窗口 14 天（含旧宇宙）
  B. 仅当前 4 币（ASTER/XRP/SOL/HYPE）
  C. 仅当前 4 币 + 最近 3 天（最接近现状）

# 用法

    python scripts/h223_era_scoped_flatten.py
"""
from __future__ import annotations

import json
import pathlib
import statistics as st

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h223_era_scoped_flatten.json"
NOW_SYMBOLS = ["ASTER", "XRP", "SOL", "HYPE"]


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
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--recent-days", type=int, default=3)
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT (meta_json->'flatten')::text,
                       coalesce(notional,0), coalesce(net_bp,0),
                       coalesce(fee_bp,0), coalesce(price_bp,0),
                       coalesce(spread_bp,0), coalesce(symbol,''), ts
                FROM lane_ledger
                WHERE lane_id=%s AND ts >= now() - (%s || ' days')::interval
                  AND meta_json IS NOT NULL AND coalesce(notional,0) > 0
                ORDER BY ts ASC
            """, (LANE, str(int(a.days))))
            raw = cur.fetchall()

    rows = []
    for flat, notl, nbp, fbp, pbp, sbp, sym, ts in raw:
        rows.append({"flat": str(flat).lower() == "true", "notional": float(notl),
                     "net_bp": float(nbp), "fee_bp": float(fbp),
                     "price_bp": float(pbp), "spread_bp": float(sbp),
                     "sym": str(sym), "ts": ts,
                     "usd": float(notl) * float(nbp) / 1e4})
    if not rows:
        print("无数据")
        return 1

    import datetime as _dt
    cut = max(r["ts"] for r in rows) - _dt.timedelta(days=a.recent_days)

    SCOPES = [
        ("A 全窗口（含旧宇宙）", rows),
        (f"B 仅当前 {len(NOW_SYMBOLS)} 币", [r for r in rows if r["sym"] in NOW_SYMBOLS]),
        (f"C 仅当前 {len(NOW_SYMBOLS)} 币 + 最近 {a.recent_days} 天",
         [r for r in rows if r["sym"] in NOW_SYMBOLS and r["ts"] >= cut]),
    ]

    print("=" * 104)
    print("H223  时代裁剪后的 flatten 成本")
    print("=" * 104)

    out = {}
    for name, sub in SCOPES:
        if not sub:
            print(f"\n  {name}：无数据")
            continue
        mk = [r for r in sub if not r["flat"]]
        fl = [r for r in sub if r["flat"]]
        tot = sum(r["usd"] for r in sub)
        print(f"\n{'━'*104}\n  {name}　{len(sub)} 腿　币种 {len(set(r['sym'] for r in sub))} 个"
              f"　总净额 **${tot:+.2f}**\n{'━'*104}")
        for lbl, g in (("maker", mk), ("flatten", fl)):
            if not g:
                print(f"    {lbl:8} 0 腿")
                continue
            nn = sum(r["notional"] for r in g)
            uu = sum(r["usd"] for r in g)
            share = len(g) / len(sub) * 100
            print(f"    {lbl:8} {len(g):>6} 腿（{share:>5.1f}%）　"
                  f"名义 ${nn:>11,.0f}　净额 ${uu:>+9.2f}　"
                  f"加权 {uu/nn*1e4 if nn else 0:>+8.3f} bp　"
                  f"单腿 ${uu/len(g):>+8.4f}")
            if lbl == "flatten":
                print(f"             成分：fee {st.mean([r['fee_bp'] for r in g]):+.2f}　"
                      f"price {st.mean([r['price_bp'] for r in g]):+.2f}　"
                      f"spread {st.mean([r['spread_bp'] for r in g]):+.2f}")
        if fl and mk:
            fl_share = sum(r["usd"] for r in fl) / tot * 100 if tot else 0
            print(f"\n    ⇒ flatten 占总亏损的 **{fl_share:.1f}%**"
                  f"　maker 净额 ${sum(r['usd'] for r in mk):+.2f}")
        out[name] = {
            "legs": len(sub), "total_usd": round(tot, 2),
            "maker_n": len(mk), "maker_usd": round(sum(r["usd"] for r in mk), 2),
            "maker_w_bp": (round(sum(r["usd"] for r in mk) /
                                 sum(r["notional"] for r in mk) * 1e4, 3)
                           if mk else None),
            "flatten_n": len(fl), "flatten_usd": round(sum(r["usd"] for r in fl), 2),
            "flatten_w_bp": (round(sum(r["usd"] for r in fl) /
                                   sum(r["notional"] for r in fl) * 1e4, 3)
                             if fl else None),
            "symbols": sorted(set(r["sym"] for r in sub)),
        }

    # ── 逐币：flatten 是普遍现象还是集中在被换掉的币 ──
    print(f"\n{'━'*104}\n  逐币：flatten 净额（谁能留下、谁要被换掉）\n{'━'*104}")
    by = {}
    for r in rows:
        by.setdefault(r["sym"], []).append(r)
    print(f"\n  {'symbol':<10}{'总腿':>7}{'flat腿':>8}{'flat净$':>11}{'maker净$':>11}"
          f"{'总净$':>11}{'flat占比':>10}  当前在宇宙")
    tot_by = []
    for s in sorted(by, key=lambda s: sum(r["usd"] for r in by[s])):
        v = by[s]
        fl = [r for r in v if r["flat"]]
        mk = [r for r in v if not r["flat"]]
        fu = sum(r["usd"] for r in fl)
        mu = sum(r["usd"] for r in mk)
        tot_by.append((s, len(v), len(fl), fu, mu, fu + mu))
        print(f"  {s:<10}{len(v):>7}{len(fl):>8}{fu:>+11.2f}{mu:>+11.2f}"
              f"{fu+mu:>+11.2f}{len(fl)/len(v)*100 if v else 0:>9.1f}%"
              f"{'  ✓' if s in NOW_SYMBOLS else '  ✗ 已移出'}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"scopes": out,
                               "by_symbol": [{"sym": s, "legs": n, "flat_n": fn,
                                              "flat_usd": round(fu, 2),
                                              "maker_usd": round(mu, 2)}
                                             for s, n, fn, fu, mu, _t in tot_by]},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
