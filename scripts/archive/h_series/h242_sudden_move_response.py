# -*- coding: utf-8 -*-
"""H242 突发事件实测：急涨急跌之后是**延续**还是**回摆**。

# 为什么必须先测这个

用户要求建立突发事件机制（暴涨/暴跌/断网）。而"检测"只是第一步，
**真正决定收益的是检测到之后做什么**，而它取决于一个纯经验问题：

    一段急涨/急跌之后，价格更可能**继续**还是**回摆**？

  · **继续** ⇒ 应当**立刻撤单 + 平掉持仓**（跟着走，别扛）
  · **回摆** ⇒ 应当**撤单但不平仓**（扛过去，因为平在极值点最差）
  · **混合** ⇒ 分级响应（先撤单，再看后续决定）

三种结论对应三种完全不同的实现，而**不需要任何新数据**就能判定。

# 现状缺口（已核实）

| 检测 | 判据 | 对突发事件的速度 |
|---|---|---|
| `trend_pause_bp=15` | `trend_move_bp(mid_hist, 20)` | 20 tick × 18-20s ≈ **6.7 分钟** ❌ |
| `vol_pause_sigma=0.7` | `realized_vol_bp(mid_hist, 20)` | 同样 6.7 分钟 ❌ |
| `ofi_block_threshold=0.5` | 上一 15s 桶 OFI | 快，但**只封下单侧** |
| `stop_loss_bp=80` | 浮亏 80bp | 唯一处理持仓的，但阈值很深 |

⇒ 价格类检测全部建在 **6.7 分钟**窗口上，而暴涨暴跌发生在**几十秒**内。
且现有闸门**只封加仓侧**，不撤已有挂单、不处理已有持仓。

# 口径（用真实逐 tick，不造代理量）

数据源 `asterdex_book_ticker`（实测 10~36 tick/s），中价 = `(bid+ask)/2`。
按**时间**而非 tick 数定义急动（tick 间隔不均匀）：

    事件点 t：`|mid_t − mid_{t−W}| / mid_{t−W} × 1e4 ≥ THR`      （W 秒内移动 ≥ THR bp）
    之后：`fwd_H = (mid_{t+H} − mid_t) / mid_t × 1e4`（带符号）

**分开看两个方向的"未来"**（对我们不利的方向才是成本）：
  · 急跌（move ≤ −THR）之后：`fwd` 为正 = 回摆（对我们多头有利），
    为负 = 继续跌
  · 急涨（move ≥ +THR）之后：`fwd` 为正 = 继续涨，为负 = 回摆

# 加强判据：合并"连续同向移动"（真正的 flash 常是多笔同向）

单纯 `|move| ≥ THR` 会把"缓慢漂移"也算进来。增加一个**聚类口径**：
在该点之前 W 秒内，**同向移动的净幅度** ≥ THR 且**单步最大移动** ≥ STEP
（即"快"而非"慢"）⇒ 更接近真实的闪崩/闪涨。

# 用法

    python scripts/h242_sudden_move_response.py --hours 6 --w 60 --thr 30
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import statistics as st
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h242_sudden_move.json"
CUR = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT"]
FWD = [15.0, 30.0, 60.0, 120.0, 300.0]


def market_dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    base = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        base = base.replace(j, "")
    return base.rsplit("/", 1)[0] + "/alpha_market"


def q(v, p):
    if not v:
        return 0.0
    s = sorted(v)
    return s[min(len(s) - 1, max(0, int(round(p / 100.0 * (len(s) - 1)))))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--w", type=float, default=60.0, help="事件窗（秒）")
    ap.add_argument("--thr", type=float, default=30.0, help="事件阈值（bp）")
    ap.add_argument("--step", type=float, default=8.0,
                    help="单步最小移动（bp）—— 区分「快」与「慢」")
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(market_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, event_ts_ms, (bid_px + ask_px) / 2.0
                FROM asterdex_book_ticker
                WHERE ingest_ts >= now() - (%s || ' hours')::interval
                  AND symbol = ANY(%s) AND bid_px > 0 AND ask_px > bid_px
                ORDER BY symbol, event_ts_ms ASC
            """, (str(float(a.hours) + 0.2), CUR))
            rows = cur.fetchall()
    if not rows:
        print("无数据")
        return 1

    per = {}
    for sym, ms, mid in rows:
        per.setdefault(str(sym), []).append((int(ms) / 1000.0, float(mid)))
    series = {s: v for s, v in per.items()}

    print("=" * 104)
    print("H242  突发事件实测：急涨急跌之后是延续还是回摆")
    print("=" * 104)
    print(f"\n  窗口 {a.hours:g}h　事件窗 W={a.w:g}s　阈值 THR={a.thr:g}bp　"
          f"单步 STEP≥{a.step:g}bp")
    for s, v in sorted(series.items()):
        print(f"  {s:12} {len(v):>8} tick　"
              f"{dt.datetime.fromtimestamp(v[0][0]):%H:%M:%S} ~ "
              f"{dt.datetime.fromtimestamp(v[-1][0]):%H:%M:%S}")

    events = []
    for sym, v in series.items():
        n = len(v)
        ts = [x[0] for x in v]
        px = [x[1] for x in v]
        j = 0
        k2 = 0
        for i in range(n):
            # 过去 W 秒的起点
            while j + 1 < i and ts[i] - ts[j + 1] >= a.w:
                j += 1
            if ts[i] - ts[j] < a.w:
                continue
            move = (px[i] - px[j]) / px[j] * 1e4 if px[j] > 0 else 0.0
            if abs(move) < a.thr:
                continue
            # 单步最大移动（判断"快"）
            step_max = 0.0
            for t in range(j + 1, i + 1):
                if px[t - 1] > 0:
                    step_max = max(step_max, abs(px[t] - px[t - 1]) / px[t - 1] * 1e4)
            if step_max < a.step:
                continue
            rec = {"sym": sym, "ts": ts[i], "move_bp": move, "step_max": step_max}
            ok = True
            for H in FWD:
                kk = i
                while kk + 1 < n and ts[kk + 1] - ts[i] < H:
                    kk += 1
                if kk == i:
                    ok = False
                    break
                rec[f"fwd_{int(H)}"] = (px[kk] - px[i]) / px[i] * 1e4
            if ok:
                events.append(rec)

    print(f"\n  检出事件 {len(events)} 个"
          f"（急涨 {sum(1 for e in events if e['move_bp']>0)} / "
          f"急跌 {sum(1 for e in events if e['move_bp']<0)}）")
    if not events:
        print("  窗口内无达标事件 ⇒ 要么行情平静，要么阈值过高")
        return 0

    # ── 一、急跌之后 ──
    print(f"\n{'━'*104}\n  一、**急跌**（{a.w:g}s 内跌 ≥{a.thr:g}bp）之后的走势\n{'━'*104}")
    dn = [e for e in events if e["move_bp"] < 0]
    print(f"\n  {'H(s)':>7}{'样本':>7}{'fwd均值bp':>12}{'fwd中位bp':>12}"
          f"{'回摆占比(>0)':>14}{'继续P25':>10}{'继续P75':>10}")
    for H in FWD:
        v = [e[f"fwd_{int(H)}"] for e in dn if f"fwd_{int(H)}" in e]
        if not v:
            continue
        pos = sum(1 for x in v if x > 0) / len(v) * 100
        print(f"  {H:>7.0f}{len(v):>7}{st.mean(v):>+12.3f}{st.median(v):>+12.3f}"
              f"{pos:>13.1f}%{q(v,25):>+10.2f}{q(v,75):>+10.2f}")
    print(f"  ⇒ 「回摆占比」>50% 且 fwd均值 >0 ⇒ 急跌后**回摆**（该扛，不该割）")

    # ── 二、急涨之后 ──
    print(f"\n{'━'*104}\n  二、**急涨**（{a.w:g}s 内涨 ≥{a.thr:g}bp）之后的走势\n{'━'*104}")
    up = [e for e in events if e["move_bp"] > 0]
    print(f"\n  {'H(s)':>7}{'样本':>7}{'fwd均值bp':>12}{'fwd中位bp':>12}"
          f"{'回摆占比(<0)':>14}{'P25':>10}{'P75':>10}")
    for H in FWD:
        v = [e[f"fwd_{int(H)}"] for e in up if f"fwd_{int(H)}" in e]
        if not v:
            continue
        neg = sum(1 for x in v if x < 0) / len(v) * 100
        print(f"  {H:>7.0f}{len(v):>7}{st.mean(v):>+12.3f}{st.median(v):>+12.3f}"
              f"{neg:>13.1f}%{q(v,25):>+10.2f}{q(v,75):>+10.2f}")
    print(f"  ⇒ 对空头持仓而言，「回摆占比」= fwd<0 的比例")

    # ── 三、对我们持仓的**净成本**（方向无关的合并口径）──
    print(f"\n{'━'*104}\n  三、做市视角：事件后「继续」的幅度 vs 「回摆」的幅度\n{'━'*104}")
    print(f"\n  {'H(s)':>7}{'急跌后|fwd|中位':>18}{'急跌后继续深度':>16}"
          f"{'急涨后|fwd|中位':>18}{'急涨后继续深度':>16}")
    for H in FWD:
        vd = [e[f"fwd_{int(H)}"] for e in dn if f"fwd_{int(H)}" in e]
        vu = [e[f"fwd_{int(H)}"] for e in up if f"fwd_{int(H)}" in e]
        if not vd or not vu:
            continue
        cont_d = q([abs(x) for x in vd if x < 0], 50)
        cont_u = q([abs(x) for x in vu if x > 0], 50)
        print(f"  {H:>7.0f}{q([abs(x) for x in vd],50):>18.2f}{cont_d:>16.2f}"
              f"{q([abs(x) for x in vu],50):>18.2f}{cont_u:>16.2f}")

    # ── 四、逐小时稳定性 ──
    print(f"\n{'━'*104}\n  四、逐小时：急跌后 H=60s 的 fwd 均值（跨窗口判据）\n{'━'*104}")
    hrs = {}
    for e in dn:
        if "fwd_60" not in e:
            continue
        h = dt.datetime.fromtimestamp(e["ts"]).strftime("%H:00")
        hrs.setdefault(h, []).append(e["fwd_60"])
    print(f"\n  {'小时':<8}{'样本':>7}{'fwd均值bp':>12}{'回摆占比':>11}")
    pos_h = 0
    nh = 0
    for h in sorted(hrs):
        v = hrs[h]
        if len(v) < 3:
            continue
        nh += 1
        p = sum(1 for x in v if x > 0) / len(v) * 100
        if p > 50:
            pos_h += 1
        print(f"  {h:<8}{len(v):>7}{st.mean(v):>+12.3f}{p:>10.1f}%")
    if nh:
        print(f"\n  ⇒ 回摆占多数的小时数 = **{pos_h}/{nh}**")

    # ── 五、结论 ──
    print(f"\n{'━'*104}\n  五、结论与响应设计含义\n{'━'*104}")
    vd60 = [e["fwd_60"] for e in dn if "fwd_60" in e]
    vu60 = [e["fwd_60"] for e in up if "fwd_60" in e]
    if vd60:
        md = st.median(vd60)
        pd_ = sum(1 for x in vd60 if x > 0) / len(vd60) * 100
        print(f"\n  急跌后 60s：中位 {md:+.3f}bp　回摆占比 {pd_:.1f}%")
    if vu60:
        mu = st.median(vu60)
        pu = sum(1 for x in vu60 if x < 0) / len(vu60) * 100
        print(f"  急涨后 60s：中位 {mu:+.3f}bp　回摆占比 {pu:.1f}%")
    print(f"\n  含义（两种情形分别对应不同的持仓处置）：")
    print(f"    · 若回摆占多数 ⇒ **撤单 + 不平仓**（平在极值点最差）")
    print(f"    · 若继续占多数 ⇒ **撤单 + 立刻平仓**（跟着走）")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "hours": a.hours, "w": a.w, "thr": a.thr, "step": a.step,
        "n_events": len(events), "n_down": len(dn), "n_up": len(up),
        "down_fwd_median": {str(int(H)): round(st.median(
            [e[f"fwd_{int(H)}"] for e in dn if f"fwd_{int(H)}" in e]), 4)
            for H in FWD if any(f"fwd_{int(H)}" in e for e in dn)},
        "up_fwd_median": {str(int(H)): round(st.median(
            [e[f"fwd_{int(H)}"] for e in up if f"fwd_{int(H)}" in e]), 4)
            for H in FWD if any(f"fwd_{int(H)}" in e for e in up)},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
