"""h488：幽灵成交的**根因定位**——`market_trades_aggregated` vs `asterdex_trades`。

发现（h487）：近 3h 的 217 笔引擎成交里 **38.5% 无法用真实逐笔验证**，且
幽灵成交中 63/84 笔的"分段极值确实越过了我们的挂单价"（引擎判据成立）。
分层归因显示**分段极值并不比真实逐笔更远**（p50 −4.03bp）⇒ 问题不在"分段太乐观"，
而在**两张表本身不一致**。

代码事实（runner.py:3102-3115）：
  引擎判成交用的分段极值来自 `market_trades_aggregated`
  （`exchange=:e AND symbol=:s AND timestamp > :lo AND timestamp <= :hi`，
   再按 `timestamp + bucket_ms > qts` 过滤 = "只算挂单之后的桶"），
  而**真实逐笔**在 `asterdex_trades`。本脚本把同一时间窗在两表里对账：

  · 若聚合表有 low_price ≤ 我们的买价、而真实逐笔的最低价**高于**它
    ⇒ **聚合表是乐观来源**（修它 = 修成交判定）；
  · 若两表一致（都有该价）⇒ 我的 h487 判定过严（窗口/方向口径问题），
    需要修的是审计而不是引擎。

用法：python scripts/h488_seg_vs_tape.py [--hours 3] [--sample 25]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASIS = ROOT / "logs" / "mm_fill_basis.jsonl"
OUT = ROOT / "research_l1" / "out" / "h488_seg_vs_tape.json"


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
    ap.add_argument("--hours", type=float, default=3.0)
    ap.add_argument("--sample", type=int, default=25)
    a = ap.parse_args()
    import time as _t
    cutoff = _t.time() - a.hours * 3600
    fills = []
    for ln in BASIS.read_text(encoding="utf-8", errors="replace").splitlines():
        if not ln.strip():
            continue
        try:
            j = json.loads(ln)
        except Exception:  # noqa: BLE001
            continue
        if float(j.get("ts") or 0) >= cutoff and float(j.get("seg_low") or 0) > 0:
            fills.append(j)
    print(f"近 {a.hours:.0f}h 有分段数据的引擎成交 {len(fills)} 笔")
    mk = read_env_dsn().replace("/alpha_arena", "/alpha_market")
    rows = []
    with psycopg.connect(mk, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='market_trades_aggregated' ORDER BY ordinal_position")
            cols = [r[0] for r in cur.fetchall()]
            print("market_trades_aggregated 列:", ", ".join(cols))
            cur.execute("SELECT DISTINCT exchange FROM market_trades_aggregated LIMIT 10")
            print("exchange 取值:", [r[0] for r in cur.fetchall()])
            cur.execute("SELECT count(*), min(timestamp), max(timestamp) "
                        "FROM market_trades_aggregated")
            print("聚合表总量/时间范围:", cur.fetchone())
            for f in fills[:a.sample]:
                sym = str(f["symbol"])
                t0 = int(float(f["ts"]) * 1000)
                px = float(f["fill_px"])
                side = f["side"]
                cur.execute(
                    "SELECT count(*), min(low_price)::float8, max(high_price)::float8, "
                    " COALESCE(sum(taker_sell_volume),0)::float8, "
                    " COALESCE(sum(taker_buy_volume),0)::float8, "
                    " min(timestamp), max(timestamp) "
                    "FROM market_trades_aggregated WHERE exchange='asterdex' "
                    "AND symbol=%s AND timestamp > %s AND timestamp <= %s",
                    (sym, t0 - 30000, t0 + 5000))
                an, alo, ahi, asv, abv, at0, at1 = cur.fetchone()
                cur.execute(
                    "SELECT count(*), min(price)::float8, max(price)::float8, "
                    " COALESCE(sum(qty) FILTER (WHERE is_buyer_maker),0)::float8, "
                    " COALESCE(sum(qty) FILTER (WHERE NOT is_buyer_maker),0)::float8 "
                    "FROM asterdex_trades WHERE symbol=%s "
                    "AND event_ts_ms > %s AND event_ts_ms <= %s",
                    (sym + "USDT", t0 - 30000, t0 + 5000))
                tn, tlo, thi, tsv, tbv = cur.fetchone()
                agg_ok = (alo is not None and float(alo) <= px) if side == "buy" else \
                         (ahi is not None and float(ahi) >= px)
                tape_ok = (tlo is not None and float(tlo) <= px) if side == "buy" else \
                          (thi is not None and float(thi) >= px)
                rows.append({"sym": sym, "side": side, "px": px,
                             "agg_n": int(an or 0), "agg_lo": alo, "agg_hi": ahi,
                             "agg_sv": asv, "agg_bv": abv, "agg_span": [at0, at1],
                             "tape_n": int(tn or 0), "tape_lo": tlo, "tape_hi": thi,
                             "agg_crosses": bool(agg_ok), "tape_crosses": bool(tape_ok)})
    if not rows:
        print("无样本")
        return 1
    print("=" * 104)
    print(f"{'sym':6s} {'side':5s} {'我们的价':>10s} {'聚合n':>5s} {'聚合lo':>10s} "
          f"{'聚合hi':>10s} {'逐笔n':>5s} {'逐笔lo':>10s} {'逐笔hi':>10s} "
          f"{'聚合过':>6s} {'逐笔过':>6s}")
    for r in rows:
        print(f"{r['sym']:6s} {r['side']:5s} {r['px']:10.5f} {r['agg_n']:5d} "
              f"{(r['agg_lo'] or 0):10.5f} {(r['agg_hi'] or 0):10.5f} "
              f"{r['tape_n']:5d} {(r['tape_lo'] or 0):10.5f} {(r['tape_hi'] or 0):10.5f} "
              f"{'是' if r['agg_crosses'] else '否':>6s} "
              f"{'是' if r['tape_crosses'] else '否':>6s}")
    agg_only = sum(1 for r in rows if r["agg_crosses"] and not r["tape_crosses"])
    both = sum(1 for r in rows if r["agg_crosses"] and r["tape_crosses"])
    tape_only = sum(1 for r in rows if r["tape_crosses"] and not r["agg_crosses"])
    neither = sum(1 for r in rows if not r["agg_crosses"] and not r["tape_crosses"])
    print("=" * 104)
    print(f"两表都支持 = {both}   仅聚合表支持 = **{agg_only}**   "
          f"仅逐笔支持 = {tape_only}   都不支持 = {neither}")
    empty_agg = sum(1 for r in rows if r["agg_n"] == 0)
    print(f"聚合表在该窗口**无任何桶**的样本 = {empty_agg}/{len(rows)}"
          f"（若比例高 ⇒ 引擎可能在用**陈旧/错位**的桶判成交）")

    # ── 追加诊断：把"都不支持"的样本按时间**回溯搜索**，看穿越发生在多久之前 ──
    #   动机（F176/F178「延迟判定」）：`judge_lag_buckets` 让引擎用**若干桶之前的挂单**
    #   去判当前窗口；若确实如此，穿越会出现在成交 ts **之前**的桶里，而不是同一窗口。
    back = []
    with psycopg.connect(mk, autocommit=True) as c:
        with c.cursor() as cur:
            for r in rows:
                if r["agg_crosses"]:
                    continue
                t0 = None
                # 找到该样本的原始 ts：从 fills 里按 (sym, px) 最近的匹配
                for f in fills:
                    if str(f["symbol"]) == r["sym"] and abs(float(f["fill_px"]) - r["px"]) < 1e-9:
                        t0 = int(float(f["ts"]) * 1000)
                        break
                if t0 is None:
                    continue
                cur.execute(
                    "SELECT timestamp, low_price::float8, high_price::float8 "
                    "FROM market_trades_aggregated WHERE exchange='asterdex' "
                    "AND symbol=%s AND timestamp > %s AND timestamp <= %s "
                    "ORDER BY timestamp DESC LIMIT 24",
                    (r["sym"], t0 - 360000, t0 + 5000))
                found = None
                for ts_ms, lo_, hi_ in cur.fetchall():
                    hit = (lo_ <= r["px"]) if r["side"] == "buy" else (hi_ >= r["px"])
                    if hit:
                        found = (int(ts_ms), (int(ts_ms) - t0) / 1000.0)
                        break
                back.append({"sym": r["sym"], "side": r["side"], "px": r["px"],
                             "found": found})
    if back:
        got = [b for b in back if b["found"]]
        print(f"\n『同窗口不支持』样本 {len(back)} 个，其中 {len(got)} 个"
              f"在**往回 6 分钟**内能找到穿越桶：")
        for b in got[:12]:
            print(f"  {b['sym']:6s} {b['side']:5s} px={b['px']:.5f} "
                  f"穿越发生在成交前 **{-b['found'][1]:.0f}s**")
        if got:
            offs = sorted(-b["found"][1] for b in got)
            print(f"  回溯偏移(s) p50={offs[len(offs)//2]:.0f} "
                  f"min={offs[0]:.0f} max={offs[-1]:.0f}")
            verdict2 = ("⇒ **成交判定确实在用更早的桶**（延迟判定/水位滞后）："
                        "穿越发生在成交 ts 之前数十秒 ⇒ 成交价是**当时的挂单价**，"
                        "与成交时刻的市场无关。这不是'幽灵'，而是**归因时刻错位**；"
                        "h487 的『幽灵』判定因此偏严，应从『成交 ts』改为"
                        "『引擎实际判定的分段窗口』")
            print(verdict2)
    if agg_only > both:
        verdict = ("聚合表 `market_trades_aggregated` 是主要乐观来源：它报出了真实逐笔"
                   "没有的极值价 ⇒ 修成交判定应从这里入手（或直接改用 `asterdex_trades`）")
    elif both >= agg_only and both > 0:
        verdict = ("两表大体一致 ⇒ h487 的『幽灵』判定偏严（窗口/方向口径），"
                   "需要修的是审计口径而不是引擎；但注意：一致 ≠ 我们一定排得到队")
    else:
        verdict = "样本不足或有异常，需人工核查"
    print("\n⇒ 裁决:", verdict)
    OUT.write_text(json.dumps({"hours": a.hours, "rows": rows,
                               "agg_only": agg_only, "both": both,
                               "tape_only": tape_only, "neither": neither,
                               "empty_agg": empty_agg, "verdict": verdict},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
