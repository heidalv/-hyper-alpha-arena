# -*- coding: utf-8 -*-
"""H209 决定性测试：控制"成交前的行情状态"之后，price_bp 是否变稳定。

# 为什么要做这个

已测并排除：

  · H207：OFI 类信号（连续 / 强度 / 逆向对齐）—— 全部平坦或跨窗口不一致
  · H207：闸门阈值扫描（0.5 → 0.95）—— 只在极端 regime 有效，是幸存者偏差
  · H208：成交规模 / 笔数 / 同向占比 —— `n` 达 +0.429bp 但跨窗口符号不一致
  · H209 本脚本的前置测试：按**持仓水平**分桶 —— 各桶 price_bp 都在 −0.10~−0.50，
    spread 也稳定 ⇒ **持仓不是主变量**

⇒ 剩下的唯一候选解释：**`price_bp` 由外部行情状态（regime）决定**。
  若如此，则"某个信号在某窗口有效、另一窗口反向"就有了统一解释：
  **那些信号只是 regime 的代理变量**，而 regime 本身才是真变量。

# 本脚本的判据（两层）

## 第一层：行情状态能否解释 price_bp 的截面差异

对每笔成交，算**成交前**窗口内的行情量（无前视）：

    rv_bp       成交前 N 秒的实现波动（15s 桶中价收益的标准差，bp）
    trend_bp    成交前 N 秒的净价格移动（bp，带符号）
    spr_bp      成交前盘口点差中位（bp）
    n_trades    成交前逐笔笔数

然后按每个量分 5 桶看 `price_bp` ⇒ 是否单调、跨度多大。

## 第二层（关键）：控制行情后，跨窗口是否稳定

这是区分"真变量"与"代理变量"的唯一办法：

  · 若把样本按 `rv_bp` 分成"低波动/高波动"两组，
    **在每组内部**看 `price_bp` 的跨窗口表现 ⇒ 若符号一致，
    说明波动是真变量，且可以据此设闸门
  · 若控制后仍不稳定 ⇒ 微观结构信号在这条车道上**根本不可平稳估计**，
    应当停止在信号方向的投入

# 用法

    python scripts/h209_regime_conditioning.py --hours 30
    python scripts/h209_regime_conditioning.py --hours 30 --window-sec 60
"""
from __future__ import annotations

import argparse
import bisect
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"


def dsn(which: str = "alpha_arena") -> str:
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
    base, _, _ = url.rpartition("/")
    return f"{base}/{which}"


def to_bare(s: str) -> str:
    return s[:-4] if s.endswith("USDT") else s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=30.0)
    ap.add_argument("--window-sec", type=int, default=60)
    ap.add_argument("--lookback-sec", type=int, default=300,
                    help="算实现波动用的回看长度")
    a = ap.parse_args()

    import psycopg

    print("=" * 100)
    print("H209  控制行情状态后，price_bp 是否变稳定")
    print("=" * 100)

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SET statement_timeout = 300000")
            cur.execute("""
                SELECT ts, symbol, lower(meta_json->>'side') AS side,
                       price_bp, spread_bp, notional
                FROM lane_ledger
                WHERE lane_id=%s AND ts > now() - make_interval(hours => %s::int)
                  AND meta_json->>'flatten' NOT IN ('true','True')
                ORDER BY symbol, ts
            """, (LANE, int(a.hours)))
            fills = [{"ts": r[0], "symbol": r[1], "side": r[2],
                      "price_bp": float(r[3] or 0), "spread_bp": float(r[4] or 0),
                      "notional": float(r[5] or 0)} for r in cur.fetchall()]
    if len(fills) < 300:
        print(f"  成交不足（{len(fills)}）")
        return 0
    syms = sorted({f["symbol"] for f in fills})
    t0 = min(f["ts"] for f in fills)
    print(f"  窗口 {a.hours:.0f}h   成交 {len(fills)} 笔   币 {syms}")

    # ── 取行情：盘口中价（裸符号）──
    with psycopg.connect(dsn("alpha_market")) as c:
        with c.cursor() as cur:
            cur.execute("SET statement_timeout = 300000")
            cur.execute("""
                SELECT symbol, timestamp,
                       (best_bid+best_ask)/2.0 AS mid,
                       (best_ask-best_bid)/NULLIF((best_bid+best_ask)/2,0)*1e4 AS spr
                FROM market_orderbook_snapshots
                WHERE exchange='asterdex' AND symbol = ANY(%s)
                  AND timestamp >= (extract(epoch FROM %s::timestamptz)*1000
                                    - %s::int*1000)
                  AND best_bid > 0 AND best_ask > best_bid
                ORDER BY symbol, timestamp
            """, ([to_bare(s) for s in syms], t0, a.lookback_sec + 60))
            books: dict = {}
            for sym, ms, mid, spr in cur.fetchall():
                books.setdefault(to_usdt_key(sym), []).append(
                    (int(ms), float(mid or 0), float(spr or 0)))

    print(f"  盘口 {sum(len(v) for v in books.values())} 行；"
          f"回看 {a.lookback_sec}s 算实现波动，特征窗口 {a.window_sec}s")
    # 诊断：键的写法与行数（符号体系混用是本项目反复踩的坑）
    if books:
        _k = sorted(books)
        print(f"  books 键（前 6）：{_k[:6]}")
        print(f"  各币盘口行数（前 6）："
              f"{ {k: len(books[k]) for k in _k[:6]} }")
        print(f"  成交的符号写法（前 3）：{syms[:3]}")
    else:
        print("  ✗ books 为空 ⇒ 盘口查询没取到数据（符号或时间窗不匹配）")
    print()

    import statistics as st

    def feats(sym: str, ts) -> dict | None:
        arr = books.get(sym)
        if not arr:
            return None
        ms = int(ts.timestamp() * 1000)
        lo = bisect.bisect_left(arr, (ms - a.lookback_sec * 1000,))
        hi = bisect.bisect_left(arr, (ms,))          # 严格早于成交
        seg = arr[lo:hi]
        if len(seg) < 6:
            return None
        mids = [s[1] for s in seg if s[1] > 0]
        if len(mids) < 6:
            return None
        rets = [(mids[i] - mids[i - 1]) / mids[i - 1] * 1e4
                for i in range(1, len(mids)) if mids[i - 1] > 0]
        if len(rets) < 3:
            return None
        # 特征窗口内的量（最近 window_sec）
        wlo = bisect.bisect_left(arr, (ms - a.window_sec * 1000,))
        wseg = arr[wlo:hi]
        if not wseg:
            return None
        trend = ((wseg[-1][1] - wseg[0][1]) / wseg[0][1] * 1e4
                 if wseg[0][1] > 0 else 0.0)
        return {
            "rv_bp": st.pstdev(rets) if len(rets) > 1 else 0.0,
            "trend_bp": trend,
            "spr_bp": st.median([s[2] for s in wseg]),
            "n_snap": len(wseg),
        }

    rows = []
    miss = 0
    for f in fills:
        # ⚠️ `books` 的键是 `ASTERUSDT`（由 `to_usdt_key` 归一），
        # 而 `f["symbol"]` 是账本写法 `ASTER` ⇒ **必须在这里转换**。
        # 首版漏了这一步：盘口明明取到 2,676 行、键也对，覆盖率却是 0。
        # （本项目符号体系混用的第 4 次：lane_ledger/market_orderbook_snapshots 用裸符号，
        #   asterdex_trades/asterdex_book_ticker 用带 USDT 的写法。）
        ft = feats(to_usdt_key(f["symbol"]), f["ts"])
        if ft:
            rows.append({**f, **ft})
        else:
            miss += 1
    print(f"  特征覆盖率 {len(rows)}/{len(fills)} "
          f"（{100.0*len(rows)/max(len(fills),1):.1f}%）  未命中 {miss}")
    if not rows:
        print("  ✗ 覆盖率 0 ⇒ 无法继续")
        return 1
    base = sum(r["price_bp"] for r in rows) / len(rows)
    print(f"  基线 avg price_bp = {base:+.4f}bp\n")

    # ── 第一层：各行情量的分位表现 ──
    def quint(key: str):
        s = sorted(rows, key=lambda r: r[key])
        n = len(s)
        out = []
        for i in range(5):
            seg = s[i * n // 5:(i + 1) * n // 5]
            if seg:
                out.append((seg[0][key], seg[-1][key],
                            sum(x["price_bp"] for x in seg) / len(seg), len(seg)))
        return out

    print("  ── 第一层：行情量的分位表现（看 price_bp 是否单调）──")
    feats_res = {}
    for key, lab in (("rv_bp", "成交前实现波动 bp"),
                     ("trend_bp", "成交前净移动 bp"),
                     ("spr_bp", "成交前点差 bp"),
                     ("n_snap", "成交前盘口更新数")):
        q = quint(key)
        if len(q) < 5:
            continue
        span = q[-1][2] - q[0][2]
        flips = sum(1 for i in range(4)
                    if (q[i + 1][2] - q[i][2]) * (q[1][2] - q[0][2]) < 0)
        feats_res[key] = (q, span, flips)
        print(f"\n  {lab}（Q5−Q1 = {span:+.3f}bp，翻转 {flips} 次）")
        for i, (lo, hi, avg, n) in enumerate(q, 1):
            print(f"    Q{i}  [{lo:>10.3f},{hi:>10.3f}]  n={n:>5}  price_bp={avg:+.3f}")

    # ── 第二层：控制行情后，跨窗口是否稳定 ──
    print("\n  ── 第二层（关键）：按行情分组后，组内跨窗口是否稳定 ──")
    key = max(feats_res.items(), key=lambda kv: abs(kv[1][1]))[0] if feats_res else None
    if not key:
        return 0
    print(f"  用最强行情量：{key}")
    med = st.median([r[key] for r in rows])
    for grp, sub in (("低 " + key, [r for r in rows if r[key] <= med]),
                     ("高 " + key, [r for r in rows if r[key] > med])):
        if len(sub) < 160:
            continue
        s = sorted(sub, key=lambda r: r["ts"])
        n = len(s)
        print(f"\n  【{grp}】n={n}  （中位切分 {med:.3f}）")
        print(f"    {'段':<20}{'腿数':>6}{'avg price_bp':>14}")
        segs = []
        for i in range(4):
            sg = s[i * n // 4:(i + 1) * n // 4]
            if len(sg) < 40:
                continue
            avg = sum(x["price_bp"] for x in sg) / len(sg)
            segs.append(avg)
            t0s = sg[0]["ts"].strftime("%m-%d %H:%M")
            t1s = sg[-1]["ts"].strftime("%H:%M")
            print(f"    {t0s}→{t1s:<9}{len(sg):>6}{avg:>+14.3f}")
        if len(segs) >= 3:
            spread = max(segs) - min(segs)
            same = all(x > 0 for x in segs) or all(x < 0 for x in segs)
            print(f"    ⇒ 段间跨度 {spread:.3f}bp，符号{'一致' if same else '不一致'}"
                  f" ⇒ {'**该组内稳定**' if same and spread < 1.0 else '仍不稳定'}")

    print("\n  判读总结：")
    print("    · 若控制行情后组内符号一致且跨度小 ⇒ **行情是真变量**，")
    print("      可据此设闸门（如'高波动时段不挂'），且它比微观结构信号更稳")
    print("    · 若控制后仍不稳定 ⇒ 微观结构信号在这条车道上不可平稳估计，")
    print("      应停止在信号方向的投入")
    return 0


def to_usdt_key(bare: str) -> str:
    """把裸符号转成 `ASTERUSDT`（账本/逐笔表的写法）。"""
    return bare if bare.endswith("USDT") else bare + "USDT"


if __name__ == "__main__":
    raise SystemExit(main())
