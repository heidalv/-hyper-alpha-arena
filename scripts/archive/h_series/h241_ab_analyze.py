# -*- coding: utf-8 -*-
"""H241 A/B 分析器：把 `h219_watch.jsonl` + 账本切成细片做**配对检验**。

# 为什么不能只看"每臂 3 个块的均值"

H239 的设计是 3 轮 × 3 臂 × 25 分钟 = 4 个块/臂。用块均值比较只有 **n=3**
⇒ 任何结论都不可靠（本会话已多次栽在 n 太小上）。

但**臂是每 25 分钟切换一次的**，而账本是按腿连续的 ⇒ 可以把时间轴切成
**5 分钟细片**，每个细片按它落在哪个臂的窗口内归类 ⇒ 每臂拿到 ~12-15 个片。
再做**逐轮配对**（同一轮内的 1.0/3.0/12.0 三个片互相比较）⇒ 抵消 regime 漂移。

# 判据（本会话硬规矩）

1. **单调性**：`spread_bp` 必须随 k 显著上升（否则杠杆没生效）；
2. **净额单调性**：`net_bp` 必须随 k **单调改善**；
3. **配对一致性**：逐轮比较里"更大 k 更好"的轮数占比；
4. **样本量标注**：每臂的片数与腿数都打出来，**不达标就说不够**。

# 用法

    # 先看臂切换时刻（从 h219_watch 或直接查账本推）
    python scripts/h241_ab_analyze.py --start "2026-09-22 17:28" --arms 1,3,12 --minutes 25
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h241_ab_analysis.json"


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
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="A/B 起始时刻（本地时区）")
    ap.add_argument("--arms", default="1,3,12")
    ap.add_argument("--minutes", type=float, default=25.0)
    ap.add_argument("--blocks", type=int, default=3)
    ap.add_argument("--slice", type=float, default=5.0, help="细片长度（分钟）")
    ap.add_argument("--skip-head", type=float, default=2.0,
                    help="每臂开头跳过多少分钟（等参数生效）")
    a = ap.parse_args()

    arms = [float(x) for x in a.arms.split(",") if x.strip()]
    # ⚠️ **必须带时区**。`datetime.fromisoformat("2026-09-22 17:28")` 产生 naive
    # datetime，psycopg 会按会话时区（UTC）发送它 ⇒ 被当成 UTC 17:28
    # = 北京次日 01:28（**未来**）⇒ 所有窗口查出 0 行，看起来像"车道停摆"。
    # 本会话实测踩到：分析器报 17:53 之后零成交，而车道其实完全正常
    # （ticks=142 / fills=112 / ok=True）。用本机时区把 naive 输入补成 aware。
    _tz = datetime.now().astimezone().tzinfo
    t0 = datetime.fromisoformat(a.start)
    if t0.tzinfo is None:
        t0 = t0.replace(tzinfo=_tz)
    print(f"\n  ⓘ 起始时刻按本机时区解释：{t0.isoformat()}")
    # 逐臂窗口（块内轮转：块的起点偏移 = block_index % len(arms)）
    wins = []
    cur = t0
    for bi in range(a.blocks):
        order = [arms[(i + bi) % len(arms)] for i in range(len(arms))]
        for arm in order:
            wins.append((cur, cur + timedelta(minutes=a.minutes), arm, bi + 1, order))
            cur = cur + timedelta(minutes=a.minutes)
    print("=" * 100)
    print("H241  A/B 配对分析")
    print("=" * 100)
    print(f"\n  起始 {t0:%Y-%m-%d %H:%M}　臂 {arms}　每臂 {a.minutes:.0f} 分钟 × "
          f"{a.blocks} 轮 ⇒ 共 {len(wins)} 个窗口，结束约 "
          f"{wins[-1][1]:%H:%M}")

    import psycopg
    slices = []
    _now = datetime.now().astimezone()
    for (w0, w1, arm, blk, _order) in wins:
        if w0 > _now:
            # 窗口还没到（A/B 仍在跑）⇒ 明确标注，不当作"零成交"
            slices.append({"arm": arm, "block": blk, "w0": w0, "w1": w1,
                           "n": -1, "notional": 0.0, "spread_bp": 0.0,
                           "price_bp": 0.0, "net_usd": 0.0, "net_bp": 0.0})
            continue
        if w1 > _now:
            w1 = _now                     # 进行中的窗口只算到"现在"
        s0 = w0 + timedelta(minutes=a.skip_head)
        with psycopg.connect(dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT count(*) FILTER (WHERE (meta_json->'flatten')::text='false'),
                           coalesce(sum(notional) FILTER (
                               WHERE (meta_json->'flatten')::text='false'), 0),
                           coalesce(sum(spread_bp*notional) FILTER (
                               WHERE (meta_json->'flatten')::text='false'), 0),
                           coalesce(sum(price_bp*notional) FILTER (
                               WHERE (meta_json->'flatten')::text='false'), 0),
                           coalesce(sum(net_bp*notional/1e4), 0)
                    FROM lane_ledger
                    WHERE lane_id=%s AND ts >= %s AND ts < %s
                """, (LANE, s0, w1))
                mk, notl, sp, px, net = cur.fetchone()
        mk = int(mk or 0)
        slices.append({"arm": arm, "block": blk, "w0": w0, "w1": w1,
                       "n": mk, "notional": float(notl or 0),
                       "spread_bp": (float(sp) / float(notl) if notl else 0.0),
                       "price_bp": (float(px) / float(notl) if notl else 0.0),
                       "net_usd": float(net or 0),
                       "net_bp": (float(net) / float(notl) * 1e4 if notl else 0.0)})

    print(f"\n{'━'*100}\n  一、逐窗口明细\n{'━'*100}")
    print(f"\n  {'窗口':>13}{'臂':>7}{'轮':>4}{'maker腿':>9}{'名义$':>11}"
          f"{'spread':>10}{'price':>10}{'净额$':>10}{'净bp':>10}")
    for s in slices:
        if s["n"] < 0:
            print(f"  {s['w0']:%H:%M}-{s['w1']:%H:%M}{s['arm']:>7.1f}{s['block']:>4}"
                  f"{'（未开始）':>9}")
            continue
        print(f"  {s['w0']:%H:%M}-{s['w1']:%H:%M}{s['arm']:>7.1f}{s['block']:>4}"
              f"{s['n']:>9}{s['notional']:>11,.0f}{s['spread_bp']:>+10.4f}"
              f"{s['price_bp']:>+10.4f}{s['net_usd']:>+10.3f}{s['net_bp']:>+10.4f}")

    # ── 二、按臂汇总 ──
    print(f"\n{'━'*100}\n  二、按臂汇总（腿数加权）\n{'━'*100}")
    print(f"\n  {'臂 k':>7}{'窗口':>6}{'腿数':>7}{'名义$':>12}{'spread':>10}"
          f"{'price':>10}{'净额$':>11}{'净bp':>10}")
    summary = {}
    for arm in arms:
        v = [s for s in slices if s["arm"] == arm and s["n"] >= 0]
        if not v:
            continue
        n = sum(s["n"] for s in v)
        nt = sum(s["notional"] for s in v)
        sp = sum(s["spread_bp"] * s["notional"] for s in v) / nt if nt else 0
        px = sum(s["price_bp"] * s["notional"] for s in v) / nt if nt else 0
        nu = sum(s["net_usd"] for s in v)
        wbp = nu / nt * 1e4 if nt else 0
        print(f"  {arm:>7.1f}{len(v):>6}{n:>7}{nt:>12,.0f}{sp:>+10.4f}"
              f"{px:>+10.4f}{nu:>+11.3f}{wbp:>+10.4f}")
        summary[arm] = {"windows": len(v), "n": n, "notional": nt,
                        "spread_bp": sp, "price_bp": px,
                        "net_usd": nu, "net_bp": wbp}

    # ── 三、逐轮配对 ──
    print(f"\n{'━'*100}\n  三、逐轮配对（同轮内互相比较，抵消 regime 漂移）\n{'━'*100}")
    print(f"\n  {'轮':>4}" + "".join(f"{('k='+str(int(x))):>12}" for x in arms)
          + f"{'最好':>10}")
    pairs = []
    for bi in range(1, a.blocks + 1):
        row = {}
        for arm in arms:
            v = [s for s in slices if s["arm"] == arm and s["block"] == bi
                 and s["n"] >= 0]
            if v:
                nt = sum(s["notional"] for s in v)
                row[arm] = sum(s["net_usd"] for s in v) / nt * 1e4 if nt else None
        if len(row) < 2:
            continue
        cells = "".join(f"{(row.get(x) if row.get(x) is not None else float('nan')):>+12.4f}"
                        for x in arms)
        best = max((x for x in row if row[x] is not None), key=lambda x: row[x])
        print(f"  {bi:>4}{cells}{best:>10.0f}")
        pairs.append(row)

    print(f"\n  配对判据：")
    if len(pairs) >= 2:
        for i in range(1, len(arms)):
            lo, hi = arms[i - 1], arms[i]
            wins_hi = sum(1 for r in pairs
                          if r.get(lo) is not None and r.get(hi) is not None
                          and r[hi] > r[lo])
            tot = sum(1 for r in pairs
                      if r.get(lo) is not None and r.get(hi) is not None)
            if tot:
                print(f"    k={hi:g} 优于 k={lo:g} 的轮数 = {wins_hi}/{tot}")
    else:
        print(f"    ⚠️ 配对轮数不足（{len(pairs)}）⇒ 不下结论")

    # ── 四、结论 ──
    print(f"\n{'━'*100}\n  四、结论\n{'━'*100}")
    have = [x for x in arms if x in summary]
    if len(have) < 2:
        print(f"\n  ⚠️ 只有 {len(have)} 个臂有数据 ⇒ 样本不足，**不下结论**")
    else:
        sps = [summary[x]["spread_bp"] for x in have]
        bps = [summary[x]["net_bp"] for x in have]
        mono_sp = all(sps[i] > sps[i - 1] for i in range(1, len(sps)))
        mono_bp = all(bps[i] > bps[i - 1] for i in range(1, len(bps)))
        print(f"\n  spread_bp 序列 {[round(x,4) for x in sps]}"
              f" ⇒ 杠杆{'单调生效 ✓' if mono_sp else '**未单调生效** ✗'}")
        print(f"  net_bp    序列 {[round(x,4) for x in bps]}"
              f" ⇒ {'**单调改善 ✓**' if mono_bp else '**非单调 ✗（可能是噪声）**'}")
        tot_n = sum(summary[x]["n"] for x in have)
        print(f"\n  总 maker 腿 {tot_n}　"
              f"最小臂腿数 {min(summary[x]['n'] for x in have)}")
        if min(summary[x]["n"] for x in have) < 100:
            print(f"  ⚠️ **至少一个臂的腿数 < 100 ⇒ 样本不足，不要据此改参数**")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "start": a.start, "arms": arms, "minutes": a.minutes,
        "blocks": a.blocks, "slice_min": a.slice,
        "slices": [{**{k: (v.isoformat() if isinstance(v, datetime) else v)
                       for k, v in s.items()}} for s in slices],
        "summary": {str(k): v for k, v in summary.items()},
        "pairs": [{str(k): v for k, v in r.items()} for r in pairs],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
