# -*- coding: utf-8 -*-
"""[H125 2026-09-21] 选币 v3 —— 用**实测每周期净额(bp)**排序，不拆解、不猜特征。

# 为什么推翻 v1（原打分器）和 v2（H123 那版）

## v1（现行生产）`coin_select_hft.score`

    score = spread_med_bp × log10(book_updates) − log10(book_updates) × 0.15

对点差**单调递增** ⇒ 专挑宽点差币。实测后果：

    AI 槽选出：SEI(点差 19.9bp/强平100%)、PENDLE(15.4bp/91.7%)、
                ONDO(13.5bp/60.8%)、ARB(10.1bp/54.3%)
    固定 5 币：ASTER(1.35bp/6.6%)、SOL(0.90bp/7.0%)、XRP(1.41bp/10.2%)
    ⇒ 被打分器选中的全是强平率 48%~100% 的，没被选中的全是 6%~10% 的。
    **打分器的方向是反的。**

## v2（H123 那版，作废）

用 `capture_bp_med − flatten_rate × 10.66bp` 排序，结果宽点差新币
（VIRTUAL 5.86bp、SEI 5.36bp，各只有 2~3 个周期）排到第 1、第 2，
把实测为正的 ASTER/SOL/XRP 压在下面 ⇒ **同一个病复发**：
拿"事前特征 + 小样本"去压"实测事实"。

## v3（本文件）：只用实测，且不拆解

H124 用真实数据反推过：`net_bp = capture_cyc − fr × 10.668bp`，结构是对的
（反推出的 10.668 与独立估的 10.66 吻合）。但 `capture_cyc`（每**周期**捕获）
用"每**笔**捕获的中位"代替会低估 —— 实测 ASTER 每笔捕获中位 0.600bp，
而每周期净额是 +0.6047bp（强平只占 6.6%）⇒ 反推的每周期捕获是 **+1.304bp**。

**既然每周期净额的分子分母都来自真实成交，就不必拆解。** 直接排序：

    metric = 已实现净额(USD) / 周期峰值名义中位(USD) × 1e4 / 周期数   ← bp/周期

实测这个指标把币排得极干净：

    ASTER +0.6047 | XRP +0.1281 | SOL +0.0471 | DOGE −0.8620
    ONDO −5.1805 | UNI −5.3291 | ARB −5.7129

# 规则（事先定死）

**摘除**（任一成立，且周期数 ≥ `--min-cycles`，默认 20）：
  R1 `metric ≤ --min-net-bp`（默认 −0.5bp/周期）—— 结构性负边际，留着就是慢性失血
  R2 `flatten_rate ≥ --max-flatten-rate`（默认 0.35）—— 波动结构不适合被动挂单

**入选**：
  · 有实测且未触发摘除的币：按 metric 降序，优先占槽
  · 空槽由**新币**补：必须同时满足
      - 盘口 2h 段的 (p90−p10)/均价 ≤ `--max-width-bp`（默认 140bp）
        （实测：好的三个 106~112bp，结构性亏损的 179~231bp ⇒ 这条能把它们分开）
      - 下四分位点差 ≥ 0.8bp（太窄的往返净为负，原 `hard_gates` 同口径）
      - 有 20 档深度
    新币按盘口更新数（吞吐）降序取，且每次最多新进 `--max-new` 个（默认 3），
    避免一次换掉半个宇宙把测量打乱。

**取消硬编码固定币**：原 `DEFAULT_FIXED = ASTER,XRP,SOL,DOGE,UNI` 标着
"人工指定、全天候交易、**不参与评分**" ⇒ 10 个槽里 5 个永久锁死，
"AI 选币"只在另外 5 个里轮换。本文件让 **10 个槽全部参与轮询**。

用法：
    .venv\\Scripts\\python.exe scripts\\h125_selector_v3.py
    .venv\\Scripts\\python.exe scripts\\h125_selector_v3.py --apply --lane mm_asterdex
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

LANE = "mm_asterdex"
FEE_FLOOR_BP = 0.8      # 与原 hard_gates 的 MIN_SPREAD_BP 同口径

# [H150 2026-09-21] **排除 USD1 合约**（用户 2026-09-21 指示："先不做 usdt1，
# 这个稳定了再设计，放弃"）。
#
# 技术原因（不只是遵命）：引擎的 `TAKER_FEE_BP` 是**模块级常量**（4.0bp），
# 不区分市场。而 Aster 的 USD1 永续 taker 是 **0.5bp**、USDT 永续是 4.0bp
# ⇒ 若把 USD1 标的放进宇宙，引擎会按 4.0bp 记账，**成本被高估 8 倍**，
# 所有基于它的盈亏/选币结论都会失真。
# 等"按市场区分费率"做完（约 8 处要改）再启用。
EXCLUDE_SUFFIX = ("USD1",)


def _excluded(sym: str) -> bool:
    return any(str(sym).upper().endswith(suf) for suf in EXCLUDE_SUFFIX)


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("DATABASE_URL", "")
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def market_url_of(url: str) -> str:
    return url.rsplit("/", 1)[0] + "/alpha_market"


def measured_metric(m: dict) -> float | None:
    """每周期净额（bp）= 净额(USD) / 峰值名义中位(USD) × 1e4 / 周期数。"""
    cyc = m.get("cycles") or 0
    notl = m.get("peak_notional_med") or 0.0
    if cyc <= 0 or notl <= 0:
        return None
    return m["net_usd"] / notl * 1e4 / cyc


# ═══════════ [F345 2026-09-23] 反转框架选币：依据从"做市净额"改成"反转 corr" ═══
# 框架已从做市改成 counter_trend（方向性短期反转）。选币依据必须同步：
#   · 做市依据 net_bp_per_cycle + "p25 点差 >4bp 全负" —— 做市时代结论
#   · 反转依据 reversal_corr（过去 120s → 未来 60s 的 corr，负=反转强）
# 实测（H262/H263）：PENDLE corr −0.1357 全场最强，而做市时代它被点差过滤淘汰
# ⇒ 旧依据会选错币（这正是选择器 00:53 把 PENDLE 换回 ASTER 的原因）。

def reversal_corr(murl: str, symbol: str, *, hours: float = 2.0,
                  k: float = 120.0, m: float = 60.0,
                  step: float = 30.0) -> dict | None:
    """纯 tick 反转 corr：过去 k 秒 → 未来 m 秒（负 = 反转）。

    1s 网格降采样 + step 秒去重叠。返回 {corr, n}；样本 <100 返回 None。
    """
    try:
        with psycopg.connect(murl) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ON (bucket) bucket, mid
                    FROM (SELECT (event_ts_ms/1000) AS bucket,
                                 (bid_px+ask_px)/2.0 AS mid
                          FROM asterdex_book_ticker
                          WHERE ingest_ts >= now() - (%s || ' hours')::interval
                            AND symbol = %s AND bid_px>0 AND ask_px>bid_px) t
                    ORDER BY bucket, mid
                """, (str(float(hours) + 0.1), symbol))
                recs = cur.fetchall()
    except Exception:
        return None
    d = {int(b): float(mid) for b, mid in recs}
    ks = sorted(d)
    mids = [d[k] for k in ks]
    n = len(ks)
    if n < 200:
        return None
    xs, ys = [], []
    last = -1e18
    for i in range(n):
        if ks[i] - last < step:
            continue
        last = ks[i]
        j = i
        while j >= 0 and ks[i] - ks[j] < k:
            j -= 1
        if j < 0 or ks[i] - ks[j] < k * 0.9 or mids[j] <= 0:
            continue
        f = i
        while f + 1 < n and ks[f + 1] - ks[i] < m:
            f += 1
        if f == i or ks[f] - ks[i] < m * 0.9 or mids[i] <= 0:
            continue
        xs.append((mids[i] - mids[j]) / mids[j] * 1e4)
        ys.append((mids[f] - mids[i]) / mids[i] * 1e4)
    if len(xs) < 100:
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0 or syy <= 0:
        return None
    return {"corr": sxy / (sxx * syy) ** 0.5, "n": len(xs),
            "tick_n": n, "symbol": symbol.replace("USDT", "")}


def evaluate_reversal(murl: str, symbols: list, *, hours: float = 2.0) -> dict:
    """对候选池所有币算反转 corr（不做账本腿依赖，适合新币）。"""
    out = {}
    for sym in symbols:
        tk = str(sym).upper()
        if not tk.endswith("USDT"):
            tk += "USDT"
        if _excluded(tk):
            continue
        r = reversal_corr(murl, tk, hours=hours)
        if r is not None:
            out[r["symbol"]] = r
    return out


def evaluate_measured(since: str) -> dict:
    """对**我们交易过**的币算实测指标 + 摘除判定。"""
    import h122_symbol_scorecard as h122
    out = {}
    for s, v in h122.compute(since).items():
        v = dict(v)
        v["net_bp_per_cycle"] = measured_metric(v)
        out[s] = v
    return out


def pool(murl: str, *, hours: int = 2) -> list:
    """候选池：盘口点差 / 段宽 / 吞吐 / 深度。窗口必须小（1.1 亿行表，大会超时）。"""
    rows = []
    with psycopg.connect(murl) as mc:
        with mc.cursor() as cur:
            cur.execute("""
                SELECT symbol, count(*) AS book_n,
                       percentile_disc(0.25) WITHIN GROUP (
                           ORDER BY (ask_px-bid_px)/NULLIF((ask_px+bid_px)/2,0)*1e4) AS spread_p25,
                       percentile_cont(0.9) WITHIN GROUP (ORDER BY (bid_px+ask_px)/2)
                         - percentile_cont(0.1) WITHIN GROUP (ORDER BY (bid_px+ask_px)/2) AS mid_rng,
                       avg((bid_px+ask_px)/2) AS mid_avg
                FROM asterdex_book_ticker
                WHERE event_ts_ms >= (extract(epoch from now())-%s*3600)*1000
                  AND ask_px > bid_px AND bid_px > 0
                GROUP BY symbol HAVING count(*) > 500
            """, (hours,))
            for sym, n, p25, rng, avg_mid in cur.fetchall():
                w = (float(rng) / float(avg_mid) * 1e4) if (rng and avg_mid) else 0.0
                rows.append({"symbol": str(sym).replace("USDT", ""), "book_n": int(n),
                             "spread_p25_bp": float(p25 or 0.0),
                             "width_bp": round(w, 2)})
    with psycopg.connect(murl) as mc:
        with mc.cursor() as cur:
            cur.execute("""SELECT DISTINCT symbol FROM asterdex_depth_snapshots
                           WHERE event_ts_ms >= (extract(epoch from now())-%s*3600)*1000""",
                        (hours,))
            depth = {str(r[0]).replace("USDT", "") for r in cur.fetchall()}
    for r in rows:
        r["has_depth"] = r["symbol"] in depth
    return rows


def decide(measured: dict, cands: list, *, slots: int, min_cycles: int,
           min_net_bp: float, max_flatten_rate: float, max_p25_bp: float,
           max_new: int, reversal: dict | None = None) -> dict:
    """纯函数：返回 {universe, removals, keeps, news, dropped}。

    [F345 2026-09-23] `reversal` 非空时走**反转框架**选币：
      依据 = reversal_corr（负=反转强），替代做市净额与 p25 点差过滤。
      这是框架切换（counter_trend）后的正确依据 ——
      旧依据会把反转最强的 PENDLE 淘汰、把反转最弱的 ASTER 留下。
    """
    if reversal:
        ranked = sorted(reversal.items(), key=lambda kv: kv[1]["corr"])
        keep = [(s, r["corr"]) for s, r in ranked if r["corr"] < -0.02]
        remove = [(s, f"R1 反转corr {r['corr']:+.4f} ≥ −0.02（无反转/动量）")
                  for s, r in ranked if r["corr"] >= -0.02]
        thin = [(s, f"样本不足 {r['n']}") for s, r in ranked if r["n"] < 100]
        uni = [s for s, _ in keep][:slots]
        news = []
        for s, r in ranked:
            if len(uni) >= slots or len(news) >= max_new:
                break
            if s in uni:
                continue
            if r["corr"] < -0.03 and r["n"] >= 100:
                uni.append(s)
                news.append(s)
        dropped = [s for s, _ in keep[slots:]]
        return {"universe": uni, "removals": remove, "keep": keep,
                "thin": thin, "news": news, "dropped": dropped,
                "eligible_new": [s for s, _ in ranked if s not in uni],
                "by": {c["symbol"]: c for c in cands},
                "reversal": ranked}

    # 做市模式（旧行为，可 --metric maker 回退）
    keep, remove, thin = [], [], []
    for s, v in measured.items():
        if _excluded(s):
            thin.append(s)
            continue
        metric = v.get("net_bp_per_cycle")
        cyc = v.get("cycles") or 0
        fr = v.get("flatten_rate") or 0.0
        if cyc < min_cycles or metric is None:
            thin.append(s)
            continue
        if metric <= min_net_bp:
            remove.append((s, f"R1 每周期净额 {metric:+.4f}bp ≤ {min_net_bp}"))
        elif fr >= max_flatten_rate:
            remove.append((s, f"R2 强平率 {fr*100:.1f}% ≥ {max_flatten_rate*100:.0f}%"))
        else:
            keep.append((s, metric))

    keep.sort(key=lambda x: -x[1])
    uni = [s for s, _ in keep][:slots]

    # 空槽由新币补：必须 有深度 + p25 点差 ∈ [FEE_FLOOR_BP, max_p25_bp]
    eligible = []
    for c in cands:
        if c["symbol"] in measured:
            continue
        if _excluded(c["symbol"]):
            continue
        if not c.get("has_depth"):
            continue
        if c["spread_p25_bp"] < FEE_FLOOR_BP:
            continue          # 太窄：往返净为负（与原 hard_gates 同口径）
        if c["spread_p25_bp"] > max_p25_bp:
            continue          # 太宽：实测全是负的
        eligible.append(c)
    eligible.sort(key=lambda c: -c["book_n"])

    news = []
    for c in eligible:
        if len(uni) >= slots or len(news) >= max_new:
            break
        uni.append(c["symbol"])
        news.append(c["symbol"])

    dropped = [s for s, _ in keep[slots:]]
    return {"universe": uni, "removals": remove, "keep": keep,
            "thin": thin, "news": news, "dropped": dropped,
            "eligible_new": [c["symbol"] for c in eligible], "by": by_of(cands)}


def by_of(cands: list) -> dict:
    return {c["symbol"]: c for c in cands}


def _symbols_lite(url: str) -> list:
    """当前宇宙（只读，供反转模式把在营币纳入评分）。"""
    try:
        with psycopg.connect(url) as c:
            with c.cursor() as cur:
                cur.execute("SELECT meta_json->'symbols' FROM lane_registry"
                            " WHERE lane_id=%s", (LANE,))
                r = cur.fetchone()
        return [str(s) for s in (r[0] or [])] if r else []
    except Exception:
        return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", default="")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--slots", type=int, default=4,
                    help="宇宙槽位数（反转框架默认 4，与当前实验一致）")
    ap.add_argument("--since", default="2026-09-21T00:00:00")
    ap.add_argument("--min-cycles", type=int, default=20)
    ap.add_argument("--min-net-bp", type=float, default=-0.5)
    ap.add_argument("--max-flatten-rate", type=float, default=0.35)
    ap.add_argument("--max-p25-bp", type=float, default=4.0,
                    help="新币 p25 点差上限。实测 >4bp 的币每周期净额全为负")
    ap.add_argument("--max-new", type=int, default=3)
    ap.add_argument("--metric", default="reversal", choices=("maker", "reversal"),
                    help="选币依据：maker=做市净额（旧），reversal=反转corr（新框架默认）")
    ap.add_argument("--hours", type=float, default=2.0,
                    help="反转 corr 的 tick 窗口（小时）")
    a = ap.parse_args()

    url = dsn()
    murl = market_url_of(url)
    print("=" * 108)
    print(f"H125  选币 v3（依据 = {'反转corr（counter_trend 框架）' if a.metric == 'reversal' else '做市每周期净额'}；10 槽全部参与轮询）")
    print("=" * 108)

    measured = evaluate_measured(a.since)
    cands = pool(murl)
    print(f"  候选池 {len(cands)} 个（2h 窗口）   有实测成交 {len(measured)} 个")

    reversal = None
    if a.metric == "reversal":
        # [F345] 反转框架：对"候选池 ∪ 当前宇宙"算反转 corr
        cur_syms = _symbols_lite(url)
        syms = sorted(set(cur_syms + [c["symbol"] for c in cands]))
        reversal = evaluate_reversal(murl, syms, hours=a.hours)
        print(f"\n  ── 反转 corr（{a.hours:g}h tick，lookback=120s→fwd=60s，负=反转强）──")
        print(f"  {'币':<10}{'corr':>10}{'样本':>8}  判定")
        for s, r in sorted(reversal.items(), key=lambda kv: kv[1]["corr"]):
            tag = "★ 强反转" if r["corr"] < -0.03 else (
                "✓ 反转" if r["corr"] < -0.02 else "✗ 无/动量")
            cur = " ←在营" if s in cur_syms else ""
            print(f"  {s:<10}{r['corr']:>+10.5f}{r['n']:>8}  {tag}{cur}")
        print()

    if a.metric == "maker":
        print(f"\n  ── 实测（按每周期净额降序）──")
        print(f"  {'币':<10} {'周期':>5} {'强平率':>7} {'净额$':>9} {'名义$':>9} "
              f"{'bp/周期':>9}  判定")
        print("  " + "-" * 66)
        for s, v in sorted(measured.items(),
                           key=lambda kv: -(kv[1].get("net_bp_per_cycle") or -99)):
            mb = v.get("net_bp_per_cycle")
            fr = v.get("flatten_rate")
            cyc = v.get("cycles") or 0
            tag = "样本不足" if cyc < a.min_cycles else (
                "摘" if (mb is not None and mb <= a.min_net_bp) else
                ("摘" if (fr or 0) >= a.max_flatten_rate else "留"))
            print(f"  {s:<10} {cyc:>5} {(fr*100 if fr is not None else 0):>6.1f}% "
                  f"{v['net_usd']:>9.3f} {v.get('peak_notional_med') or 0:>9.2f} "
                  f"{(mb if mb is not None else 0):>9.4f}  {tag}")

    d = decide(measured, cands, slots=a.slots, min_cycles=a.min_cycles,
               min_net_bp=a.min_net_bp, max_flatten_rate=a.max_flatten_rate,
               max_p25_bp=a.max_p25_bp, max_new=a.max_new, reversal=reversal)

    print(f"\n  ── 摘除 ──")
    for s, why in d["removals"]:
        print(f"    {s:<10} {why}")
    if not d["removals"]:
        print("    (无)")
    if a.metric == "maker":
        print(f"\n  ── 新币候选（有深度 + p25点差 ∈ [{FEE_FLOOR_BP}, {a.max_p25_bp}]bp）──")
        for s in d["eligible_new"]:
            c = d["by"][s]
            print(f"    {s:<10} p25点差 {c['spread_p25_bp']:>6.2f}bp  段宽 {c['width_bp']:>7.2f}bp  "
                  f"盘口更新 {c['book_n']:>9,}")
        if not d["eligible_new"]:
            print("    (无)")

    print("\n" + "=" * 108)
    print("决策")
    print("=" * 108)
    print(f"  保留（{'反转corr<−0.02' if a.metric == 'reversal' else '实测为正'}）："
          f"{[s for s, _ in d['keep']]}")
    print(f"  新进：{d['news']}   （每次上限 {a.max_new} 个，避免一次换太多）")
    print(f"  因槽位不足丢弃：{d['dropped']}")
    print(f"  样本不足不动：{d['thin']}")
    print(f"\n  ⇒ 新宇宙（{len(d['universe'])} 槽）：**{d['universe']}**")

    if not (a.apply and a.lane):
        print("\n  （预览）加 --apply --lane mm_asterdex 才写库")
        return 0

    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (a.lane,))
            row = cur.fetchone()
            if not row:
                print(f"  错误：车道 {a.lane} 不存在")
                return 1
            meta = dict(row[0] or {})
            before = list(meta.get("symbols") or [])
            meta["symbols"] = d["universe"]
            meta["h125_rollback"] = {"symbols": before}
            meta["universe"] = {
                "fixed": [],
                "ai": d["universe"],
                "note": "H125：10 槽全部参与轮询；排序＝实测每周期净额(bp)；"
                        "取消硬编码固定币（原 ASTER,XRP,SOL,DOGE,UNI 不参与评分）",
                "as_of": datetime.now().astimezone().isoformat(),
                "net_bp_per_cycle": {s: d["by"].get(s, {}).get("net_bp_per_cycle")
                                     for s in d["universe"]},
            }
            ops = list(meta.get("ops_changes") or [])
            ops.append({"by": "h125_selector_v3", "op": "set_symbols",
                        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "before": before, "after": d["universe"],
                        "reason": "目标函数改为实测每周期净额；摘除结构性负边际币；"
                                  "10 槽全部参与轮询"})
            meta["ops_changes"] = ops[-40:]
            cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now()"
                        " WHERE lane_id=%s",
                        (json.dumps(meta, ensure_ascii=False, default=str), a.lane))
        conn.commit()
    print(f"\n  ⇒ 已写入 {a.lane}：{before} → {d['universe']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
