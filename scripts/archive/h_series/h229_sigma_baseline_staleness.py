# -*- coding: utf-8 -*-
"""H229 σ 闸的基准是否已过期 —— 用真实盘口重算当前 realized_vol_bp。

# 现场（实测心跳）

    replay_baseline.as_of = 2026-09-20T07:59:32        ← 2.5 天前冻结
    vol_baseline_bp = {ARB:11.72, SEI:3.92, SOL:3.27, UNI:9.03, XRP:3.91,
                       DOGE:3.41, ASTER:3.67, PENDLE:7.55, VIRTUAL:4.54, 1000SHIB:4.18}
      ↑ 其中 6 个币**已不在宇宙**；当前宇宙的 **HYPE 没有基准值**
    limits.vol_pause_sigma = 0.7
    avg_sigma_all = 1.227      sigma_decisions = {all: 136, quoted: 31}
    lane_pause_counts = {vol_pause: 94}（同一窗口内的车道级触发次数）

⇒ **只有 23% 的决策真的挂了单**，车道级 σ 闸在持续触发。

# 机制

σ 闸用的是**相对**口径（`runner.py`）：

    sigma = max(0, realized_vol_bp / vol_baseline_bp − 1)
    if sigma > vol_pause_sigma:  → 整车道暂停

⇒ 基准是在某个**平静期**校准的（`window_days=14` 的**中位数**），
一旦市场波动水平整体上一个台阶，`sigma` 就会**持续 > 阈值** ⇒ 车道大部分时间停摆。
这不是"闸门在保护我们"，而是"刻度没跟着走"。

# 本脚本做两件事

1. 用 `asterdex_book_ticker`（真实盘口）**按 runner 同口径**重算当前逐币
   `realized_vol_bp`（15s 网格 → 20 期窗口），与冻结基准对比；
2. 给出"按当前市场重标定"后的基准值，便于判断闸门刻度差多少倍。

# 口径（必须与 runner 一致，否则重算无意义）

`replay_baseline.method` 写明：
    mid 按 15s 网格取末值 → compute_vol_baselines(window=20)
    → 20×15s=5min 窗口的 realized_vol_bp 中位

本脚本照抄这条链：15s 网格取末值 → 逐 20 期窗口算 realized_vol_bp → 取中位。

# 用法

    python scripts/h229_sigma_baseline_staleness.py --hours 6
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "research_l1" / "out" / "h229_sigma_baseline.json"
CUR = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT"]


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


def registry_baseline() -> dict:
    import psycopg
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    u = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        u = u.replace(j, "")
    with psycopg.connect(u) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'replay_baseline' FROM lane_registry"
                        " WHERE lane_id='mm_asterdex'")
            return dict(cur.fetchone()[0] or {})


def realized_vol_bp(px, window=20, grid_s=15):
    """与 runner 同口径：15s 网格上取末值后，逐窗口的 realized_vol_bp 序列。"""
    # 15s 网格
    grid = {}
    for sec, p in px:
        grid[sec // grid_s * grid_s] = p
    ks = sorted(grid)
    vals = [grid[k] for k in ks]
    out = []
    for i in range(len(vals) - window):
        w = vals[i:i + window + 1]
        rets = [(w[j + 1] - w[j]) / w[j] * 1e4 for j in range(len(w) - 1) if w[j] > 0]
        if len(rets) < 2:
            continue
        out.append(st.pstdev(rets) * (len(rets) ** 0.5))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    a = ap.parse_args()

    rb = registry_baseline()
    base = dict(rb.get("vol_baseline_bp") or {})
    print("=" * 100)
    print("H229  σ 闸的基准是否已过期")
    print("=" * 100)
    print(f"\n  注册表基准 as_of = {rb.get('as_of')}　method = {str(rb.get('method'))[:60]}…")
    print(f"  基准含 {len(base)} 个币，其中已不在宇宙："
          f"{[s for s in base if s not in ('ASTER','XRP','SOL','HYPE')]}")

    import psycopg
    with psycopg.connect(market_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, event_ts_ms, (bid_px + ask_px) / 2.0
                FROM asterdex_book_ticker
                WHERE ingest_ts >= now() - (%s || ' hours')::interval
                  AND symbol = ANY(%s) AND bid_px > 0 AND ask_px > 0
                ORDER BY symbol, event_ts_ms ASC
            """, (str(float(a.hours)), CUR))
            rows = cur.fetchall()

    per = {}
    for sym, ms, mid in rows:
        per.setdefault(str(sym), {})[int(ms // 1000)] = float(mid)
    series = {s: sorted(d.items()) for s, d in per.items()}

    print(f"\n{'━'*100}\n  一、当前 realized_vol_bp vs 冻结基准\n{'━'*100}")
    print(f"\n  {'币':<12}{'冻结基准bp':>12}{'当前realized中位':>18}"
          f"{'当前P90':>10}{'ratio中位':>11}{'σ=ratio-1':>11}{'闸>0.7?':>9}")
    res = {}
    for sym, items in sorted(series.items()):
        bare = sym.replace("USDT", "")
        rv = realized_vol_bp(items)
        if not rv:
            continue
        med = st.median(rv)
        p90 = sorted(rv)[int(len(rv) * 0.9)]
        b = base.get(bare)
        if b and b > 0:
            ratio = med / b
            sig = max(0.0, ratio - 1.0)
            flag = "**是**" if sig > 0.7 else "否"
            print(f"  {sym:<12}{b:>12.3f}{med:>18.3f}{p90:>10.3f}"
                  f"{ratio:>11.2f}{sig:>11.2f}{flag:>9}")
            res[bare] = {"baseline": b, "now_median": round(med, 3),
                         "ratio": round(ratio, 3), "sigma": round(sig, 3),
                         "gate_0.7": sig > 0.7}
        else:
            print(f"  {sym:<12}{'（无基准）':>12}{med:>18.3f}{p90:>10.3f}"
                  f"{'—':>11}{'0.00':>11}{'否（恒不触发）':>9}")
            res[bare] = {"baseline": None, "now_median": round(med, 3),
                         "note": "无基准 ⇒ σ 恒为 0 ⇒ 该币永不被车道 σ 闸拦"}

    # ── 二、结论 ──
    print(f"\n{'━'*100}\n  二、结论\n{'━'*100}")
    gated = [k for k, v in res.items() if v.get("gate_0.7")]
    nobase = [k for k, v in res.items() if v.get("baseline") is None]
    print(f"\n  当前宇宙 {len(res)} 币中：")
    print(f"    · σ > 0.7（会被车道 σ 闸拦）= **{gated or '无'}**")
    print(f"    · 无基准值（闸门对其失效）  = **{nobase or '无'}**")
    if gated:
        print(f"\n  ⇒ **基准已过期**：冻结于 {str(rb.get('as_of'))[:19]} 的刻度"
              f"无法代表当前市场，")
        print(f"     导致车道级 σ 闸**持续触发**（实测 `lane_pause_counts.vol_pause`"
              f" 在 32 个 tick 内触发 94 次）。")
        print(f"     ⇒ 后果：只有 23% 的决策真的挂单"
              f"（`sigma_decisions = {{all:136, quoted:31}}`）。")
        print(f"\n  ⚠️ 这**不是**「闸门在保护我们」，而是「刻度没跟着走」——"
              f"闸门停的是**全部报价**，")
        print(f"     包括本可以做市赚钱的那部分。")
    else:
        print(f"\n  ⇒ 基准未过期（当前波动未超阈值）")

    # ── 三、重标定建议值 ──
    print(f"\n{'━'*100}\n  三、若重标定，基准应取何值（供参考，不自动写）\n{'━'*100}")
    print(f"\n  {'币':<10}{'旧基准':>10}{'新建议(=当前中位)':>20}{'倍数':>9}")
    for k, v in sorted(res.items()):
        if v.get("baseline") is None:
            print(f"  {k:<10}{'—':>10}{v['now_median']:>20.3f}{'新增':>9}")
        else:
            print(f"  {k:<10}{v['baseline']:>10.3f}{v['now_median']:>20.3f}"
                  f"{v['ratio']:>9.2f}×")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"as_of": rb.get("as_of"), "hours": a.hours,
                               "current": res, "gated": gated, "no_baseline": nobase},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
