"""h498：**止损队列刻画**——33 笔止损是不是"可以提前识别的同一类"。

为什么做：h497 的按路径美元分解给出唯一结论——
**`stop_loss_taker` 是单条最大失血线**（近 12h：33 腿、−50.2bp/腿、**−14.85$**，
占主动平仓总亏损 115%；h472 之后 5 腿更是 −72.8bp/腿）。
而 h470 已证明"不止损、等 300s 被动出场"的结果几乎一样（Δ=−0.55bp, t=−0.08）
⇒ 亏损由**入场**决定，止损只是把它变现。于是关键问题变成：
**止损队列在入场时能否被识别？**（若能，就该在入场侧过滤，而不是继续动止损参数。）

本脚本用 h494/h496 的累计持仓口径重建往返，把**以止损收口的往返**与**其余往返**对比：
  ① 入场腿构成：几笔 OPEN/ADD、加仓名义、首次开仓到止损的时长；
  ② 币种/时段集中度（是否某个币或某个小时特别容易吃止损）；
  ③ 止损腿的 net_bp 分布（尾部形状）；
  ④ 入场时的市况代理（用该往返首腿的 |price_bp|、spread_bp 与加仓深度做对照）。

用法：python scripts/h498_stop_cohort.py [--hours 24]
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
OUT = ROOT / "research_l1" / "out" / "h498_stop_cohort.json"


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
    ap.add_argument("--hours", type=float, default=24.0)
    a = ap.parse_args()
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT ts, symbol, COALESCE(meta_json->>'side',''), "
                "ABS(COALESCE((meta_json->>'qty')::float8,0))::float8, "
                "COALESCE(meta_json->>'exit_path',''), COALESCE(net_bp,0)::float8, "
                "COALESCE(spread_bp,0)::float8, COALESCE(price_bp,0)::float8, "
                "COALESCE(fee_bp,0)::float8, COALESCE(notional,0)::float8 "
                "FROM lane_ledger WHERE lane_id=%s "
                "AND ts > now() - make_interval(hours => %s::int) ORDER BY symbol, ts",
                (LANE, int(a.hours)))
            rows = cur.fetchall()
    by_sym = collections.OrderedDict()
    for r in rows:
        by_sym.setdefault(r[1], []).append(r)
    trips = []
    for sym, rs in by_sym.items():
        cum = 0.0
        peak = 0.0
        t = None
        for ts, _s, side, qty, ep, net, sp, px, fee, noti in rs:
            signed = abs(qty) if str(side).lower() == "buy" else -abs(qty)
            before = cum
            cum += signed
            peak = max(peak, abs(cum))
            if t is None:
                t = {"sym": sym, "open": ts, "legs": [], "peak": peak}
            if ep != "":
                kind = "FLATTEN"
            elif abs(before) < 1e-9:
                kind = "OPEN"
            elif (before > 0) == (signed > 0):
                kind = "ADD"
            else:
                kind = "REDUCE"
            t["legs"].append({"ts": ts, "kind": kind, "ep": ep, "net": net,
                              "sp": sp, "px": px, "fee": fee, "noti": noti})
            if abs(cum) <= max(1e-6, 1e-4 * max(peak, 1e-9)):
                t["close"] = ts
                t["hold"] = (ts - t["open"]).total_seconds()
                flats = [lg for lg in t["legs"] if lg["kind"] == "FLATTEN"]
                t["closed_by"] = (flats[-1]["ep"] if flats else "REDUCE")
                trips.append(t)
                t = None
                peak = 0.0
    print(f"近 {a.hours:.0f}h：{len(trips)} 个往返")
    stops = [t for t in trips if t["closed_by"].startswith("stop_loss")]
    other = [t for t in trips if not t["closed_by"].startswith("stop_loss")]
    print(f"  以**止损**收口 = {len(stops)}（{100.0*len(stops)/max(len(trips),1):.1f}%）"
          f" | 其余 = {len(other)}")
    if not stops:
        print("窗口内无止损往返")
        return 0

    def profile(name, ts_):
        if not ts_:
            return {}
        adds = [sum(1 for lg in t["legs"] if lg["kind"] == "ADD") for t in ts_]
        opens = [sum(1 for lg in t["legs"] if lg["kind"] == "OPEN") for t in ts_]
        reds = [sum(1 for lg in t["legs"] if lg["kind"] == "REDUCE") for t in ts_]
        holds = [t["hold"] for t in ts_ if t.get("hold") is not None]
        entry_noti = [sum(lg["noti"] for lg in t["legs"] if lg["kind"] in ("OPEN", "ADD"))
                      for t in ts_]
        print(f"\n── {name}（n={len(ts_)}）──")
        print(f"  腿数/往返 中位={st.median([len(t['legs']) for t in ts_]):.0f} | "
              f"OPEN {st.mean(opens):.2f} / ADD {st.mean(adds):.2f} / "
              f"REDUCE {st.mean(reds):.2f}")
        print(f"  持仓时长 中位={st.median(holds):.0f}s" if holds else "  （无持仓数据）")
        print(f"  入场名义合计 中位={st.median(entry_noti):.0f}$ "
              f"均值={st.mean(entry_noti):.0f}$")
        return {"n": len(ts_), "open_mean": round(st.mean(opens), 2),
                "add_mean": round(st.mean(adds), 2), "reduce_mean": round(st.mean(reds), 2),
                "hold_median": round(st.median(holds), 1) if holds else None,
                "entry_notional_median": round(st.median(entry_noti), 1)}

    ps = profile("止损队列", stops)
    po = profile("其余往返", other)
    # 币种 / 时段集中度
    print("\n币种集中度：")
    cs = collections.Counter(t["sym"] for t in stops)
    co = collections.Counter(t["sym"] for t in other)
    print(f"{'币':6s} {'止损数':>7s} {'其余':>7s} {'止损占比':>9s}")
    for sym in sorted(set(cs) | set(co)):
        s_, o_ = cs.get(sym, 0), co.get(sym, 0)
        print(f"{sym:6s} {s_:7d} {o_:7d} {100.0*s_/max(s_+o_,1):8.1f}%")
    print("\n时段集中度（本地小时）：")
    hs = collections.Counter(t["open"].astimezone().hour for t in stops)
    ho = collections.Counter(t["open"].astimezone().hour for t in other)
    worst_hours = sorted(hs.items(), key=lambda kv: -kv[1])[:5]
    print(f"  止损最多的时段: {[(f'{h}:00', c) for h, c in worst_hours]}")
    nets = [lg["net"] for t in stops for lg in t["legs"] if lg["kind"] == "FLATTEN"]
    nets.sort()
    print(f"\n止损腿 net_bp 分布：p10={nets[int(.1*(len(nets)-1))]:.1f} "
          f"中位={st.median(nets):.1f} p90={nets[int(.9*(len(nets)-1))]:.1f} "
          f"min={nets[0]:.1f} max={nets[-1]:.1f}")
    usd = sum(lg["net"] * lg["noti"] / 1e4 for t in stops for lg in t["legs"]
              if lg["kind"] == "FLATTEN")
    print(f"止损总净额 = {usd:+.2f}$（近 {a.hours:.0f}h）")
    # 结论（两个方向都要判，第一版只判了"更大/更多"）
    ratio_add = ps.get("add_mean", 0) / max(po.get("add_mean", 1e-9), 1e-9)
    ratio_noti = ps.get("entry_notional_median", 0) / max(
        po.get("entry_notional_median", 1e-9), 1e-9)
    if ratio_noti < 0.7 or ratio_add < 0.7:
        verdict = (f"止损仓**更小更快**（入场名义中位 {ps.get('entry_notional_median')}$ vs "
                   f"{po.get('entry_notional_median')}$ = {ratio_noti:.2f}×；"
                   f"加仓腿 {ps.get('add_mean')} vs {po.get('add_mean')} = {ratio_add:.2f}×；"
                   f"持仓 {ps.get('hold_median')}s vs {po.get('hold_median')}s）"
                   f"⇒ 止损发生在**首腿之后不久**（还没加仓就被打穿）"
                   f"⇒ 这是**入场时点/挂单位置**问题（填单逆向选择），不是持仓规模问题；"
                   f"动止损阈值或加仓节奏都治不了")
    elif ratio_noti > 1.3 or ratio_add > 1.3:
        verdict = ("止损仓在入场时**明显更重/加仓更多** ⇒ 可从「仓位规模/加仓节奏」侧识别"
                   "（例如水下禁止加仓）")
    else:
        verdict = ("止损仓与其余仓在入场规模/节奏上接近 ⇒ 需回到入场信号侧过滤")
    top_sym = max(cs.items(), key=lambda kv: kv[1]) if cs else ("-", 0)
    sym_share = 100.0 * top_sym[1] / max(len(stops), 1)
    print(f"\n★ 集中度：**{top_sym[0]} 一个币占全部止损的 {sym_share:.0f}%**"
          f"（{top_sym[1]}/{len(stops)}）⇒ 币种层面的杠杆值得单独评估")
    print("\n⇒ 裁决:", verdict)
    OUT.write_text(json.dumps(
        {"hours": a.hours, "trips": len(trips), "stops": len(stops),
         "stop_profile": ps, "other_profile": po,
         "stop_by_symbol": dict(cs), "other_by_symbol": dict(co),
         "stop_by_hour": {str(k): v for k, v in hs.items()},
         "stop_net_usd": round(usd, 3), "verdict": verdict},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    # ── 附加：分币种净额（判断止损集中的币是不是整体在亏）──
    print("\n分币种净额与显著性（同窗口，全部腿）：")
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT symbol, count(*), "
                "COALESCE(sum(net_bp*notional/1e4),0)::float8, "
                "COALESCE(avg(net_bp),0)::float8, COALESCE(sum(notional),0)::float8, "
                "COALESCE(stddev_samp(net_bp),0)::float8 "
                "FROM lane_ledger WHERE lane_id=%s "
                "AND ts > now() - make_interval(hours => %s::int) "
                "GROUP BY 1 ORDER BY 3", (LANE, int(a.hours)))
            resp = cur.fetchall()
            # 当前宇宙（用于标注"在役/已下线"）
            cur.execute("SELECT meta_json->'symbols' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            _row = cur.fetchone()
            live = set((_row[0] if _row and isinstance(_row[0], list) else []) or [])
    import math as _m
    print(f"{'币':6s} {'在役':>4s} {'腿数':>6s} {'净额$':>9s} {'净bp/腿':>9s} "
          f"{'t':>7s} {'名义$':>10s}")
    sig = []
    for sym, n, usd_, nb, noti, sd in resp:
        n = int(n)
        se = (float(sd) / _m.sqrt(n)) if (sd and n > 1) else float("nan")
        t = (float(nb) / se) if se and se == se and se > 0 else float("nan")
        flag = "✓" if sym in live else "—"
        print(f"{sym:6s} {flag:>4s} {n:6d} {float(usd_):9.2f} {float(nb):9.2f} "
              f"{t:7.2f} {float(noti):10.0f}")
        if sym in live and t == t and abs(t) >= 1.5:
            sig.append((sym, round(float(nb), 3), round(t, 2), n))
    if sig:
        print(f"\n★ 在役币里**统计上偏离 0**（|t|≥1.5）的：{sig}")
        print("  ⇒ 可作为「币种质量重配」的证据基础；但动币种会直接影响 ≥60 腿/h 硬约束，"
              "必须以「腾出的额度投向更好的币」为前提，不能简单删币。")
    else:
        print("\n★ 在役币里没有 |t|≥1.5 的显著偏离 ⇒ **不要据此删币**（差异可能是噪声）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
