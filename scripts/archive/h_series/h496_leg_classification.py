"""h496：**按持仓轨迹重新分类每一腿**（OPEN/ADD/REDUCE/FLATTEN）并重算资金去向。

为什么要推翻旧口径（这是本轮最重要的发现）：
  此前所有分解（h471/h486/h468）都把成交按 **`exit_path` 是否为空** 分成
  "入场腿 / 出场腿"。但 h495 逐腿明细显示：**被动减仓成交是非 flatten 的**
  （引擎挂减仓单、对手方来吃 ⇒ 这笔成交 `exit_path=''`，与开仓腿无法区分）
  ⇒ 旧口径的"入场腿"里**混着被动出库腿**，"入场腿只赚 +0.14bp"这类结论
  因此是**两种腿的混合**，不能直接当作"入场捕获"。

新口径（只用账本自身字段，靠**带符号累计持仓**判方向）：
  逐 symbol 遍历，维护 `cum`；对每一腿：
      cum_before == 0                ⇒ **OPEN**（开仓）
      sign(腿) == sign(cum_before)   ⇒ **ADD**（加仓）
      sign(腿) != sign(cum_before)   ⇒ **REDUCE**（被动减仓/回摆出库）
      且 `exit_path != ''`            ⇒ **FLATTEN**（引擎主动平仓：止损/超时/择时…）
  （FLATTEN 与 REDUCE 可能同时成立：flatten 行本身就是主动平仓，优先归 FLATTEN。）

产出：四类腿各自的 腿数 / 价差 / 价格 / 手续费 / 净额 / 名义 与 **美元贡献**，
以及"往返"（flat→flat）的时长与出场构成。用它才能回答"钱在哪个环节流出"。

用法：python scripts/h496_leg_classification.py [--hours 12]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import statistics as st
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h496_leg_class.json"


def read_env_dsn() -> str:
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
    ap.add_argument("--hours", type=float, default=12.0)
    a = ap.parse_args()
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT ts, symbol, COALESCE(meta_json->>'side',''), "
                "ABS(COALESCE((meta_json->>'qty')::float8,0))::float8, "
                "COALESCE(meta_json->>'exit_path',''), "
                "COALESCE(spread_bp,0)::float8, COALESCE(price_bp,0)::float8, "
                "COALESCE(fee_bp,0)::float8, COALESCE(net_bp,0)::float8, "
                "COALESCE(notional,0)::float8 "
                "FROM lane_ledger WHERE lane_id=%s "
                "AND ts > now() - make_interval(hours => %s::int) "
                "ORDER BY symbol, ts", (LANE, int(a.hours)))
            rows = cur.fetchall()
    by_sym = collections.OrderedDict()
    for r in rows:
        by_sym.setdefault(r[1], []).append(r)
    cls: dict = collections.defaultdict(
        lambda: {"n": 0, "sp": [], "px": [], "fee": [], "net": [], "usd": 0.0,
                 "notional": 0.0, "paths": collections.Counter()})
    trips = []
    for sym, rs in by_sym.items():
        cum = 0.0
        peak = 0.0
        trip_open = None
        trip_legs = 0
        for ts, _s, side, qty, ep, sp, px, fee, net, noti in rs:
            signed = abs(qty) if str(side).lower() == "buy" else -abs(qty)
            before = cum
            cum += signed
            if ep != "":
                k = "FLATTEN"
            elif abs(before) < 1e-9:
                k = "OPEN"
            elif (before > 0) == (signed > 0):
                k = "ADD"
            else:
                k = "REDUCE"
            d = cls[k]
            d["n"] += 1
            d["sp"].append(sp)
            d["px"].append(px)
            d["fee"].append(fee)
            d["net"].append(net)
            d["usd"] += net * noti / 1e4
            d["notional"] += noti
            if ep:
                d["paths"][ep] += 1
            # 往返（flat→flat，含相对容差，避免残量把多笔往返粘成一笔）
            peak = max(peak, abs(cum))
            if trip_open is None and abs(before) < 1e-9 and abs(signed) > 0:
                trip_open = ts
                trip_legs = 1
            elif trip_open is not None:
                trip_legs += 1
            if trip_open is not None and abs(cum) <= max(1e-6, 1e-4 * max(peak, 1e-9)):
                trips.append({"sym": sym, "open": trip_open, "close": ts,
                              "hold": (ts - trip_open).total_seconds(),
                              "legs": trip_legs, "closed_by": ep or "REDUCE"})
                trip_open = None
                peak = 0.0
    tot_n = sum(d["n"] for d in cls.values())
    print(f"近 {a.hours:.0f}h：{tot_n} 腿，{len(trips)} 个往返（flat→flat）")
    print("=" * 108)
    print(f"{'类别':>9s} {'腿数':>6s} {'占比':>6s} {'价差bp':>8s} {'价格bp':>8s} "
          f"{'费bp':>8s} {'净bp':>8s} {'名义$':>10s} {'净额$':>9s} {'净额占比':>9s}")
    tot_usd = sum(d["usd"] for d in cls.values())
    for k in ("OPEN", "ADD", "REDUCE", "FLATTEN"):
        d = cls.get(k)
        if not d or d["n"] == 0:
            continue
        m = lambda v: st.mean(v) if v else 0.0        # noqa: E731
        print(f"{k:>9s} {d['n']:6d} {100.0*d['n']/tot_n:5.1f}% {m(d['sp']):8.2f} "
              f"{m(d['px']):8.2f} {m(d['fee']):8.2f} {m(d['net']):8.2f} "
              f"{d['notional']:10.0f} {d['usd']:9.2f} "
              f"{100.0*d['usd']/tot_usd if tot_usd else 0:8.1f}%")
    print("=" * 108)
    print(f"合计净额 = {tot_usd:+.2f}$")
    for k in ("FLATTEN",):
        d = cls.get(k)
        if d and d["paths"]:
            print(f"{k} 的出口构成:", dict(d["paths"].most_common(8)))
    if trips:
        holds = sorted(t["hold"] for t in trips)
        legs = [t["legs"] for t in trips]
        print(f"往返：持仓 p10/p50/p90/max = "
              f"{holds[int(.1*(len(holds)-1))]:.0f}/{st.median(holds):.0f}/"
              f"{holds[int(.9*(len(holds)-1))]:.0f}/{holds[-1]:.0f} s"
              f" | 腿数中位 {st.median(legs):.0f}")
        over = sum(1 for h in holds if h > 320)
        print(f"  >320s 的往返 = {over}（相对容差已修 ⇒ 残量粘连应显著减少）")
        by = collections.Counter(t["closed_by"] for t in trips)
        print("  收口方式:", dict(by.most_common(8)))
    # 关键结论：真正的"入场捕获"只能用 OPEN+ADD
    ent = [v for k in ("OPEN", "ADD") for v in cls[k]["sp"]]
    red = [v for k in ("REDUCE", "FLATTEN") for v in cls[k]["sp"]]
    if ent:
        print("=" * 108)
        print(f"**真·入场腿（OPEN+ADD）价差捕获均值 = {st.mean(ent):+.2f}bp（n={len(ent)}）**")
        print(f"  vs 出库腿（REDUCE+FLATTEN）价差 = {st.mean(red):+.2f}bp（n={len(red)}）")
        print("  ⇒ h486 里「捕获率 0.51」用的是**混了被动出库的旧分类**，需按此重算："
              f"{st.mean(ent):+.2f} / 报价半宽")
    OUT.write_text(json.dumps(
        {"hours": a.hours, "legs": tot_n, "trips": len(trips),
         "by_class": {k: {"n": d["n"], "spread_bp": round(st.mean(d["sp"]), 3) if d["sp"] else None,
                          "price_bp": round(st.mean(d["px"]), 3) if d["px"] else None,
                          "fee_bp": round(st.mean(d["fee"]), 3) if d["fee"] else None,
                          "net_bp": round(st.mean(d["net"]), 3) if d["net"] else None,
                          "notional_usd": round(d["notional"], 1), "net_usd": round(d["usd"], 3),
                          "paths": dict(d["paths"].most_common(8))}
                      for k, d in cls.items()},
         "net_usd_total": round(tot_usd, 3),
         "entry_spread_bp": round(st.mean(ent), 3) if ent else None,
         "exit_spread_bp": round(st.mean(red), 3) if red else None},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
