# -*- coding: utf-8 -*-
"""H207 用我们自己的数据检验：哪些信号真的能预测逆向选择（price_bp）。

# 待检验的假设

本仓库已确立（H206）：

    · maker 腿  spread +0.21bp   price −0.20bp   net ≈ 0
    · `corr(穿越深度, price_bp) = −0.094` ⇒ **不是记账口径问题**（机制 A）
    ⇒ 要改善 net，必须**用信号预测 price_bp，在毒性高时不挂**

引擎里已接线但**关闭**的两个候选闸门：

    mp_block_bp       = 0.0   （microprice 偏离闸）
    flow_persist_pause = 0    （多桶持续性单边流闸）

以及已开启的 `ofi_block_threshold = 0.5`（单桶 OFI）。

# 外部证据（文献/业界）

· [Amberdata 的订单簿失衡分析](https://blog.amberdata.io/beyond-the-spread-understanding-market-impact-and-execution)
  报告"极端失衡超过 ±30%（占 11.9% 的观测）**未能产生有意义的价格移动**"
  ⇒ 若在我们的场所也成立，则**基于 OFI 的闸门本就不该有效**，需要别的信号
· [微价格作为公允价（Stoikov 系）](https://ora.ox.ac.uk/objects/uuid:cdab1de2-7576-42e2-abae-ab12371eba76/files/r1n79h621q)
· [LOB 机制对做市策略的影响](https://ar5iv.labs.arxiv.org/html/2502.18625v1)

⚠️ 本脚本**只做我们自己的数据检验**。文献给的是候选信号与先验，
**是否在我们的场所/我们的腿量下有效，必须实测**（业界的 ±30% 结论
是在更深的市场里测的，不一定适用）。

# 方法：事后重建成交时的市场状态（因为 fill_notes 没落库）

`fill_notes` 是内存环且**不含 OFI/microprice** ⇒ 无法直接查。
但两个时间戳都在，可以**重建**：

  成交        → `lane_ledger.ts`            （timestamptz）
  主动买卖量  → `market_trades_aggregated`  （毫秒；15s 桶）
  盘口/微价格 → `market_orderbook_snapshots`（毫秒；约 20s）

对每笔成交，取**成交前最近的一个已完成 15s 桶**算 OFI：

    OFI = (taker_buy_volume − taker_sell_volume) / (两者之和)   ∈ [−1, 1]

⚠️ 用"成交**前**的桶"是刻意的：避免用到未来信息（前视偏差）。

# 判据

对每个候选信号，按分位分桶算 `price_bp` 的均值：
**若单调 ⇒ 该信号可用；若平坦 ⇒ 不可用。**

# 用法

    python scripts/h207_signal_predicts_adverse_selection.py --hours 24
    python scripts/h207_signal_predicts_adverse_selection.py --hours 24 --symbol ASTERUSDT
"""
from __future__ import annotations

import argparse
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


def load_fills(hours: float, symbol: str | None) -> list:
    import psycopg
    q = """
        SELECT ts, symbol, lower(meta_json->>'side') AS side,
               (meta_json->>'qty')::float AS qty,
               (meta_json->>'fill_px')::float AS fpx,
               (meta_json->>'mid_px')::float  AS mpx,
               price_bp, spread_bp, net_bp, notional
        FROM lane_ledger
        WHERE lane_id=%s AND ts > now() - make_interval(hours => %s::int)
          AND meta_json->>'flatten' NOT IN ('true','True')
          AND (meta_json->>'fill_px') IS NOT NULL
    """
    args = [LANE, int(hours)]
    if symbol:
        q += " AND symbol=%s"
        args.append(symbol)
    q += " ORDER BY ts"
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SET statement_timeout = 180000")
            cur.execute(q, args)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


def build_features(fills: list) -> dict:
    """给每笔成交配一个**成交前**的 15s 桶 OFI（避免前视）。"""
    import psycopg
    if not fills:
        return {}
    syms = sorted({f["symbol"] for f in fills})
    t0 = min(f["ts"] for f in fills)
    # 取一段提前量，保证第一笔成交也有"前一个桶"
    with psycopg.connect(dsn("alpha_market")) as c:
        with c.cursor() as cur:
            cur.execute("SET statement_timeout = 180000")
            cur.execute("""
                SELECT symbol, timestamp,
                       coalesce(taker_buy_volume,0), coalesce(taker_sell_volume,0),
                       coalesce(low_price,0), coalesce(high_price,0)
                FROM market_trades_aggregated
                WHERE exchange='asterdex' AND symbol = ANY(%s)
                  AND timestamp >= (extract(epoch FROM %s::timestamptz)*1000 - 3600000)
                ORDER BY symbol, timestamp
            """, (syms, t0))
            buckets: dict = {}
            for sym, ts_ms, bv, sv, lo, hi in cur.fetchall():
                buckets.setdefault(sym, []).append(
                    (int(ts_ms), float(bv), float(sv), float(lo), float(hi)))
    return buckets


def ofi_before(buckets: dict, sym: str, ts) -> tuple:
    """成交**前**最近一个已完成 15s 桶的 (OFI, taker_sell>0, taker_buy>0)。"""
    arr = buckets.get(sym)
    if not arr:
        return (None, None, None)
    ms = int(ts.timestamp() * 1000)
    # 桶时间戳是桶起点；要求桶**完全早于**成交 ⇒ 桶起点 + 15s <= 成交时刻
    best = None
    for b in arr:
        if b[0] + 15000 <= ms:
            if best is None or b[0] > best[0]:
                best = b
        elif b[0] > ms:
            break
    if best is None:
        return (None, None, None)
    tot = best[1] + best[2]
    if tot <= 0:
        return (0.0, best[2] > 0, best[1] > 0)
    return ((best[1] - best[2]) / tot, best[2] > 0, best[1] > 0)


def report_signal(name: str, pairs: list) -> None:
    """`pairs` = [(信号值, price_bp)]；按分位分 5 桶看 price_bp 的均值。"""
    pairs = [(x, y) for x, y in pairs if x is not None]
    if len(pairs) < 50:
        print(f"  {name:<26} 样本不足（n={len(pairs)}）")
        return
    pairs.sort(key=lambda p: p[0])
    n = len(pairs)
    print(f"  {name}（n={n}）")
    print(f"    {'分位桶':<12}{'信号范围':>20}{'腿数':>7}{'avg price_bp':>14}")
    k = 5
    for i in range(k):
        seg = pairs[i * n // k:(i + 1) * n // k]
        if not seg:
            continue
        lo, hi = seg[0][0], seg[-1][0]
        avg = sum(p[1] for p in seg) / len(seg)
        print(f"    Q{i+1:<10}{f'[{lo:+.3f},{hi:+.3f}]':>20}{len(seg):>7}{avg:>+14.3f}")
    # 单调性：Q1→Q5 的落差与符号变化次数
    means = []
    for i in range(k):
        seg = pairs[i * n // k:(i + 1) * n // k]
        if seg:
            means.append(sum(p[1] for p in seg) / len(seg))
    if len(means) >= 3:
        span = means[-1] - means[0]
        flips = sum(1 for i in range(len(means) - 1)
                    if (means[i + 1] - means[i]) * (means[1] - means[0]) < 0)
        verdict = ("**单调可用**" if flips == 0 and abs(span) > 0.3
                   else "有分化但非单调" if abs(span) > 0.3
                   else "**平坦（不可用）**")
        print(f"    ⇒ Q5−Q1 = {span:+.3f}bp，方向翻转 {flips} 次 ⇒ {verdict}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--symbol", default="")
    a = ap.parse_args()

    print("=" * 100)
    print("H207  哪些信号真的能预测 price_bp（用我们自己的数据）")
    print("=" * 100)
    fills = load_fills(a.hours, a.symbol or None)
    print(f"  窗口 {a.hours:.0f}h   成交 {len(fills)} 笔"
          f"{'   币=' + a.symbol if a.symbol else ''}")
    if not fills:
        return 0
    base = sum(float(f["price_bp"] or 0) for f in fills) / len(fills)
    print(f"  全体 avg price_bp = {base:+.4f}bp（这是要解释的目标）\n")

    buckets = build_features(fills)
    # 逐笔配特征
    rows = []
    for f in fills:
        ofi, sell_any, buy_any = ofi_before(buckets, f["symbol"], f["ts"])
        rows.append({**f, "ofi": ofi, "sell_any": sell_any, "buy_any": buy_any})

    cov = sum(1 for r in rows if r["ofi"] is not None)
    print(f"  特征覆盖率：{cov}/{len(rows)} （{100.0*cov/max(len(rows),1):.1f}%）\n")

    # ── 信号 1：上一桶 OFI（引擎已有闸门 ofi_block_threshold=0.5）──
    report_signal("上一桶 OFI（连续）",
                  [(r["ofi"], float(r["price_bp"] or 0)) for r in rows])

    # ── 信号 2：OFI 的绝对强度（毒性强度，不看方向）──
    report_signal("|上一桶 OFI|（强度）",
                  [(abs(r["ofi"]) if r["ofi"] is not None else None,
                    float(r["price_bp"] or 0)) for r in rows])

    # ── 信号 3：OFI 的**方向与成交侧的一致性**（真正的逆向选择判据）──
    #   买腿遇上 OFI<0（卖压）⇒ 被逆向选择；卖腿遇上 OFI>0（买压）⇒ 被逆向选择
    def adverse_align(r):
        if r["ofi"] is None or not r["side"]:
            return None
        s = 1.0 if r["side"] == "buy" else -1.0
        return -s * r["ofi"]      # >0 = 与成交侧相反（不利）
    report_signal("逆向对齐度 −side×OFI", [(adverse_align(r),
                                          float(r["price_bp"] or 0)) for r in rows])

    # ── 信号 4：引擎实际用的那个闸门口径（|OFI|>0.5 分组）──
    grp = {"|OFI|>0.5（闸门会封）": [], "|OFI|≤0.5（闸门放行）": []}
    for r in rows:
        if r["ofi"] is None:
            continue
        k = "|OFI|>0.5（闸门会封）" if abs(r["ofi"]) > 0.5 else "|OFI|≤0.5（闸门放行）"
        grp[k].append(float(r["price_bp"] or 0))
    print("  引擎现有闸门口径（ofi_block_threshold=0.5）")
    print(f"    {'组':<24}{'腿数':>7}{'avg price_bp':>14}{'判定'}")
    for k, v in grp.items():
        if not v:
            continue
        avg = sum(v) / len(v)
        print(f"    {k:<24}{len(v):>7}{avg:>+14.3f}")
    if all(grp.values()):
        a1 = sum(grp["|OFI|>0.5（闸门会封）"]) / len(grp["|OFI|>0.5（闸门会封）"])
        a2 = sum(grp["|OFI|≤0.5（闸门放行）"]) / len(grp["|OFI|≤0.5（闸门放行）"])
        d = a1 - a2
        print(f"    ⇒ 被封组的 price_bp 比放行组 {'差' if d < 0 else '好'} "
              f"{abs(d):.3f}bp")
        print(f"      {'**闸门有效**（被封的确实更差）' if d < -0.1 else '**闸门无效**（两组无差别）' if abs(d) <= 0.1 else '方向相反，需查口径'}")

    print("\n  判读总结：")
    print("    · 若某个信号单调且跨度 > 0.3bp ⇒ 值得接线并设阈值")
    print("    · 若全部平坦 ⇒ 与我们可得的信号无关，需要新技术（LOB 微观结构）")
    print("    · 特别注意：`ofi_block_threshold=0.5` 若被判为无效，")
    print("      应关掉它（它现在只是在减少成交量，没在防亏）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
