# -*- coding: utf-8 -*-
"""H253 决定性测量：逆选择成本随**当前波动**的放大规律。

# ADA 实验暴露的机制（这是本会话最重要的因果发现）

| | 我们的 spread | 逆选择 price | net_bp | 单腿$ |
|---|---|---|---|---|
| 当前四币（Δ≈0.28bp） | +0.19 | **−1.18** | ~−1.0 | −$0.0368 |
| **ADA（Δ≈2bp）** | **+0.71**（5 倍） | **−5.93**（**5 倍**） | −5.47 | −$0.0633 |

⇒ **逆选择不是常数**（H247 假设它 ≈ −1.3bp），而是**随被触达所需的价格移动放大**：
要触达 Δ=2bp 的挂单，价格必须先走 2bp，而**走了 2bp 之后的后续漂移，比走 0.2bp 之后大得多**。

⇒ 这条规律同样解释了用户的**原始诉求**：
**「遇到大波动行情，直接就大亏」** —— 大波动 = 逆选择成本被放大。

# 本脚本测什么

对每一腿（账本真实成交），配一个**同时刻、同币的波动度量**（用 tick 数据），
然后看 `price_bp`（逆选择）如何随波动变化。

**波动量**（三个候选，都要试，因为"哪个才是引擎真正相关的"是实证问题）：
  · `rv_15s`  ：成交前 15 秒的已实现波动（1s 网格）
  · `rng_60s` ：成交前 60 秒的价差幅度
  · `absmove_15s`：成交前 15 秒的净移动绝对值

# 为什么这个测量能直接给出可执行的闸门

若 `E[price_bp | 波动]` 单调恶化，且存在一个波动水平使
`spread_bp + price_bp < 0` ⇒ 那个水平就是**闸门阈值**，
而且它是**用账本真实成交 + 真实波动**测出来的，不是模型推的。

⇒ 这是本会话第一次把"波动"与"逆选择成本"**直接**连起来测。

# 用法

    python scripts/h253_adverse_vs_vol.py --hours 12
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h253_adverse_vs_vol.json"
LANE = "mm_asterdex"
CUR = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT", "ADAUSDT"]


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


def market_dsn() -> str:
    return dsn().rsplit("/", 1)[0] + "/alpha_market"


def q(v, p):
    if not v:
        return 0.0
    s = sorted(v)
    return s[min(len(s) - 1, max(0, int(round(p / 100.0 * (len(s) - 1)))))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=12.0)
    a = ap.parse_args()

    import psycopg
    # ── 取 maker 腿 ──
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, ts, coalesce(spread_bp,0), coalesce(price_bp,0),
                       coalesce(net_bp,0), coalesce(notional,0)
                FROM lane_ledger
                WHERE lane_id=%s AND (meta_json->'flatten')::text='false'
                  AND ts >= now() - (%s || ' hours')::interval
                  AND coalesce(notional,0) > 0
                ORDER BY ts ASC
            """, (LANE, str(float(a.hours))))
            legs = cur.fetchall()
    if not legs:
        print("窗口内无 maker 腿")
        return 1

    # ── 取 tick（逐币，1s 降采样）──
    PX = {}
    for sym in CUR:
        try:
            with psycopg.connect(market_dsn()) as c:
                with c.cursor() as cur:
                    cur.execute("""
                        SELECT DISTINCT ON (bucket) bucket, bid_px, ask_px
                        FROM (SELECT (event_ts_ms/1000) AS bucket, bid_px, ask_px
                              FROM asterdex_book_ticker
                              WHERE ingest_ts >= now() - (%s || ' hours')::interval
                                AND symbol = %s AND bid_px>0 AND ask_px>bid_px) t
                        ORDER BY bucket, bid_px
                    """, (str(float(a.hours) + 0.3), sym))
                    PX[sym] = {int(b): ((float(x) + float(y)) / 2.0)
                               for b, x, y in cur.fetchall()}
        except Exception as e:
            print(f"  ⚠️ {sym} 拉取失败：{str(e)[:60]}")

    print("=" * 104)
    print("H253  逆选择成本随当前波动的放大规律")
    print("=" * 104)
    print(f"\n  窗口 {a.hours:g}h　maker 腿 {len(legs)}　"
          f"tick 覆盖 {len(PX)} 币")

    rows = []
    for sym, ts, sp, px_, net, notl in legs:
        tk = str(sym).upper()
        if not tk.endswith("USDT"):
            tk += "USDT"
        d = PX.get(tk)
        if not d:
            continue
        t = int(ts.timestamp())
        now_mid = d.get(t)
        if now_mid is None:
            near = [k for k in d if abs(k - t) <= 3]
            if not near:
                continue
            t = min(near, key=lambda k: abs(k - t))
            now_mid = d[t]
        # 前 15s / 60s 的 mid 序列
        w15 = [d[k] for k in sorted(d) if t - 15 <= k <= t]
        w60 = [d[k] for k in sorted(d) if t - 60 <= k <= t]
        if len(w15) < 3 or len(w60) < 5:
            continue
        rets = [(w15[i] - w15[i - 1]) / w15[i - 1] * 1e4
                for i in range(1, len(w15)) if w15[i - 1] > 0]
        if len(rets) < 2:
            continue
        rv15 = st.pstdev(rets)
        rng60 = (max(w60) - min(w60)) / w60[0] * 1e4 if w60[0] > 0 else 0.0
        mv15 = abs(w15[-1] - w15[0]) / w15[0] * 1e4 if w15[0] > 0 else 0.0
        rows.append({"sym": str(sym), "spread": float(sp), "price": float(px_),
                     "net": float(net), "notional": float(notl),
                     "rv15": rv15, "rng60": rng60, "mv15": mv15})

    if not rows:
        print("无法匹配 tick ⇒ 检查 symbol 命名（账本用裸名，tick 用 USDT 后缀）")
        return 1
    print(f"  匹配成功 {len(rows)} 腿")

    for key, label in (("rv15", "成交前 15s 已实现波动（1s 收益 std，bp）"),
                       ("mv15", "成交前 15s 净移动 |Δ|（bp）"),
                       ("rng60", "成交前 60s 价差幅度（bp）")):
        vals = sorted(r[key] for r in rows)
        n = len(vals)
        EDGES = [0.0] + [vals[int(n * p)] for p in (0.2, 0.4, 0.6, 0.8)] + [1e9]
        print(f"\n{'━'*104}\n  {label}\n{'━'*104}")
        print(f"\n  {'波动档':>16}{'腿数':>7}{'spread均值':>12}{'**逆选择price均值**':>19}"
              f"{'net均值':>10}{'折每腿$':>11}")
        for i in range(len(EDGES) - 1):
            lo, hi = EDGES[i], EDGES[i + 1]
            sel = [r for r in rows if lo <= r[key] < hi]
            if len(sel) < 20:
                continue
            nn = sum(r["notional"] for r in sel)
            usd = sum(r["notional"] * r["net"] / 1e4 for r in sel)
            lbl = f"{lo:.2f}–{hi:.2f}" if hi < 1e9 else f"{lo:.2f}+"
            print(f"  {lbl:>16}{len(sel):>7}"
                  f"{st.mean([r['spread'] for r in sel]):>+12.4f}"
                  f"{st.mean([r['price'] for r in sel]):>+19.4f}"
                  f"{st.mean([r['net'] for r in sel]):>+10.4f}"
                  f"{usd/nn*1e4 if False else usd/len(sel):>+11.4f}")
        # 转正点
        ok = []
        for i in range(len(EDGES) - 1):
            lo, hi = EDGES[i], EDGES[i + 1]
            sel = [r for r in rows if lo <= r[key] < hi]
            if len(sel) < 20:
                continue
            m = st.mean([r["net"] for r in sel])
            ok.append((lo, hi, m, len(sel)))
        pos = [x for x in ok if x[2] > 0]
        if pos:
            print(f"\n  ⇒ **net 为正的最低档 = {pos[0][0]:.2f} 起**（{len(pos)}/{len(ok)} 档为正）")
            cov = sum(x[3] for x in pos) / sum(x[3] for x in ok) * 100
            print(f"     覆盖 = **{cov:.1f}%** 的成交")
            print(f"     ⇒ 可实现闸门：`{key} < {pos[0][0]:.2f}` ⇒ 不挂单")
        else:
            print(f"\n  ⇒ **没有任何波动档为正** ⇒ 波动分档不能解决")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "n": len(rows)}, ensure_ascii=False,
                              indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
