# -*- coding: utf-8 -*-
"""[H123 2026-09-21] 选币目标函数重做 —— 用**已实现每周期净额**，不再用「点差×吞吐」。

# 病在哪（实测证据，不是推测）

现行 `coin_select_hft.score`：

    score = spread_med_bp × log10(book_updates) − log10(book_updates) × 0.15

**对点差单调递增**。于是它选出来的 AI 槽是：

    SEI     中位点差 **19.67bp**   → 强平率 100%（n=2）
    PENDLE  点差 4bp 级             → 强平率 **91.7%**（n=12）
    ONDO                           → 强平率 **62.0%**（n=50）
    ARB                            → 强平率 **54.3%**（n=140）
    VIRTUAL                        → 强平率 100%（n=3）

而固定 5 币（人工指定、不参与评分）恰好是最好的三个：

    ASTER 强平率 **6.4%** 净 **+4.79 USD**
    SOL   强平率 **6.7%** 净 +0.70
    XRP   强平率 **10.2%** 净 +0.83

**⇒ 打分器专挑被强平的那批。** 经济含义很直白：点差宽 ⇒ 波动大 ⇒ 价格在我们
挂单成交前穿过去 ⇒ 最后按 taker 强平（`stop_loss` / `max_one_side`），
4.36bp 的 taker 费 + 逆向移动把 maker 捕获全吃掉。

# 新目标函数

不用事前特征猜，直接用**我们能测的东西**：

    E[每周期净额] = capture_bp − flatten_rate × (taker_fee_bp + 逆向移动 bp)

  · `capture_bp`   —— 该币实测的入场腿价差捕获（`mm_fill_basis.edge_bp` 中位）
  · `flatten_rate` —— 该币实测强平率（H84 周期口径）
  · 逆向移动 6.3bp —— H102 实测每周期强平成本 ≈ 10.7bp，其中 4.36bp 是 taker 费

**没有历史成交的新币**：不能用"实测"，只能给**保守上限估计**：

    E ≈ spread_p25 × 0.5 − flatten_rate_prior × 10.66bp

只用点差的**下四分位**（不是中位）打对折 ⇒ 新币必须"连 p25 都够宽"才入选，
**而不是**因为中位点差宽就入选。`flatten_rate_prior` 由**段宽**估：
H112 实测段宽最窄档强平率 6.7%、最宽档 11.6%；但 PENDLE/ARB 那类 50%~90%
**不是段宽能解释的** ⇒ 先验必须保守（封顶 35%）。

# 保留的部分（不推翻）

`hard_gates`（点差下限、盘口新鲜度、有深度）继续用 —— 那是"能不能挂"的硬条件，
与目标函数无关。

用法：
    .venv\\Scripts\\python.exe scripts\\h123_selector_v2.py
    .venv\\Scripts\\python.exe scripts\\h123_selector_v2.py --apply --lane mm_asterdex
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
TAKER_FEE_BP = 4.36          # Aster perp taker（实测口径）
FLATTEN_ADVERSE_BP = 6.3     # 强平时的额外逆向移动（见模块 docstring）
FLATTEN_PRIOR_LO = 0.07      # 段宽最窄档的强平率先验（H112 实测 6.7%）
FLATTEN_PRIOR_HI = 0.35      # 段宽最宽档的先验上限（保守）


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


def candidate_stats(market_url: str, *, hours: int = 2) -> list:
    """候选池：最近 `hours` 小时有盘口数据 + 有 20 档深度的币。

    ⚠️ 窗口**必须小**：`asterdex_book_ticker` 约 1.1 亿行，24 小时的加权分位
    查询实测直接超时（>120s）。现成的 `compute_hft_stats` 用近 **2 小时**
    （约 20s），并把中位/下四分位一次算完 ⇒ 这里沿用同一口径。

    段宽（σ 代理）也放进同一次扫描，避免为每个币再跑一遍全表 ⇒ 一次查询拿全部。
    """
    out = []
    with psycopg.connect(market_url) as mc:
        with mc.cursor() as cur:
            cur.execute("""
                SELECT symbol,
                       count(*) AS book_n,
                       percentile_disc(0.5) WITHIN GROUP (
                           ORDER BY (ask_px-bid_px)/NULLIF((ask_px+bid_px)/2,0)*1e4) AS spread_med,
                       percentile_disc(0.25) WITHIN GROUP (
                           ORDER BY (ask_px-bid_px)/NULLIF((ask_px+bid_px)/2,0)*1e4) AS spread_p25,
                       percentile_cont(0.9) WITHIN GROUP (ORDER BY (bid_px+ask_px)/2)
                         - percentile_cont(0.1) WITHIN GROUP (ORDER BY (bid_px+ask_px)/2) AS mid_range,
                       avg((bid_px+ask_px)/2) AS mid_avg
                FROM asterdex_book_ticker
                WHERE event_ts_ms >= (extract(epoch from now())-%s*3600)*1000
                  AND ask_px > bid_px AND bid_px > 0
                GROUP BY symbol
                HAVING count(*) > 500
            """, (hours,))
            for sym, n, med, p25, rng, avg_mid in cur.fetchall():
                w = (float(rng) / float(avg_mid) * 1e4) if (rng and avg_mid) else 0.0
                out.append({"symbol": str(sym).replace("USDT", ""), "book_n": int(n),
                            "spread_med_bp": float(med or 0.0),
                            "spread_p25_bp": float(p25 or 0.0),
                            "width_bp_6h": round(w, 3)})
    with psycopg.connect(market_url) as mc:
        with mc.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT symbol FROM asterdex_depth_snapshots
                WHERE event_ts_ms >= (extract(epoch from now())-%s*3600)*1000
            """, (hours,))
            has_depth = {str(r[0]).replace("USDT", "") for r in cur.fetchall()}
    for d in out:
        d["has_depth"] = d["symbol"] in has_depth
    return out


def realized_stats(since_iso: str) -> dict:
    """我们**自己交易过**的币的实测绩效（复用 H122 的口径，单一真相）。"""
    import h122_symbol_scorecard as h122
    res = {}
    for s, v in h122.compute(since_iso).items():
        vv = dict(v)
        vt, why = h122.verdict(vv)
        vv["verdict"], vv["verdict_reason"] = vt, why
        res[s] = vv
    return res


def seg_width_bp(market_url: str, sym: str, *, hours: int = 2) -> float:
    """段宽代理（bp）：盘口中价的 (p90−p10)/均价。

    ⚠️ 正常情况下 `candidate_stats` 已经在同一次扫描里算好了这段宽，
    本函数只在需要单独查某个币时使用（例如候选池被 `HAVING` 挡掉的币）。
    """
    with psycopg.connect(market_url) as mc:
        with mc.cursor() as cur:
            cur.execute("""
                SELECT percentile_cont(0.9) WITHIN GROUP (ORDER BY (bid_px+ask_px)/2)
                       - percentile_cont(0.1) WITHIN GROUP (ORDER BY (bid_px+ask_px)/2),
                       avg((bid_px+ask_px)/2)
                FROM asterdex_book_ticker
                WHERE symbol=%s
                  AND event_ts_ms >= (extract(epoch from now())-%s*3600)*1000
                  AND ask_px > bid_px AND bid_px > 0
            """, (sym + "USDT", hours))
            r = cur.fetchone()
            if not r or not r[1]:
                return 0.0
            return (float(r[0] or 0.0) / float(r[1])) * 1e4


def prior_flatten_rate(width_bp: float) -> float:
    """段宽 → 强平率先验（H112 实测两端点线性插值；保守封顶）。"""
    if width_bp <= 2.13:
        return FLATTEN_PRIOR_LO
    if width_bp >= 6.76:
        return FLATTEN_PRIOR_HI
    frac = (width_bp - 2.13) / (6.76 - 2.13)
    return FLATTEN_PRIOR_LO + frac * (FLATTEN_PRIOR_HI - FLATTEN_PRIOR_LO)


def expectancy_bp(row: dict, real: dict, width_bp: float) -> tuple:
    """返回 (E[每周期净额 bp], 依据说明)。"""
    s = row["symbol"]
    cost_per = TAKER_FEE_BP + FLATTEN_ADVERSE_BP
    if s in real and (real[s].get("cycles") or 0) >= 30:
        m = real[s]
        cap = m.get("capture_bp_med") or 0.0
        fr = m.get("flatten_rate") or 0.0
        return (cap - fr * cost_per,
                f"实测：捕获 {cap:+.2f}bp − 强平 {fr*100:.1f}%×{cost_per:.2f}bp")
    cap = (row.get("spread_p25_bp") or 0.0) * 0.5
    fr = prior_flatten_rate(width_bp)
    return (cap - fr * cost_per,
            f"新币先验：p25点差×0.5 = {cap:.2f}bp − 先验强平 {fr*100:.1f}%×{cost_per:.2f}bp")


def decide(rows: list, slots: int) -> tuple:
    """返回 (新宇宙, 摘除列表)。纯函数，便于测试。"""
    ok = [r for r in rows if "expectancy_bp" in r]
    ok.sort(key=lambda r: -r["expectancy_bp"])
    removals = [r["symbol"] for r in ok if r.get("verdict") == "remove"]
    new_uni = [r["symbol"] for r in ok if r.get("verdict") != "remove"][:slots]
    return new_uni, removals


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", default="")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--slots", type=int, default=10, help="槽位数（默认 10，全部参与轮询）")
    ap.add_argument("--hours", type=int, default=2,
                    help="盘口统计窗口（小时）。≥6 会因 1.1 亿行而超时，勿随意加大")
    ap.add_argument("--since", default="2026-09-21T00:00:00")
    a = ap.parse_args()

    url = dsn()
    market_url = url.rsplit("/", 1)[0] + "/alpha_market"
    print("=" * 108)
    print("H123  选币 v2（目标＝已实现每周期净额，不再追点差）")
    print("=" * 108)

    real = realized_stats(a.since)
    cands = candidate_stats(market_url, hours=a.hours)
    print(f"  候选池（{a.hours}h 内有盘口且更新>1000）：**{len(cands)}** 个")
    print(f"  有实测成交的币：{len(real)} 个")

    rows = []
    for r in cands:
        if not r.get("has_depth"):
            r["skip"] = "无 20 档深度"
            rows.append(r)
            continue
        w = float(r.get("width_bp_6h") or 0.0)
        if not w:
            try:
                w = seg_width_bp(market_url, r["symbol"], hours=a.hours)
            except Exception:
                w = 0.0
        e, why = expectancy_bp(r, real, w)
        r["width_bp_6h"] = round(w, 3)
        r["expectancy_bp"] = round(e, 4)
        r["why"] = why
        if r["symbol"] in real:
            v = real[r["symbol"]]
            r["verdict"] = v.get("verdict")
            r["cycles"] = v.get("cycles")
            r["flatten_rate"] = v.get("flatten_rate")
        rows.append(r)

    ok = sorted([r for r in rows if "expectancy_bp" in r],
                key=lambda r: -r["expectancy_bp"])
    print(f"\n  {'币':<10} {'E[净]bp':>9} {'点差中位':>9} {'p25':>7} {'段宽':>7} "
          f"{'实测周期':>8} {'强平率':>7} {'判定':>6}")
    print("  " + "-" * 84)
    for r in ok:
        fr = f"{r['flatten_rate']*100:.1f}%" if r.get("flatten_rate") is not None else "—"
        print(f"  {r['symbol']:<10} {r['expectancy_bp']:>9.3f} {r['spread_med_bp']:>9.2f} "
              f"{r['spread_p25_bp']:>7.2f} {r.get('width_bp_6h', 0):>7.2f} "
              f"{r.get('cycles') or 0:>8} {fr:>7} {r.get('verdict') or '新':>6}")

    new_uni, removals = decide(rows, a.slots)
    print("\n" + "=" * 108)
    print("决策")
    print("=" * 108)
    print(f"  实测判「摘除」：{removals or '(无)'}")
    print(f"  新宇宙（{len(new_uni)} 槽）：{new_uni}")
    skipped = [r["symbol"] for r in ok[:a.slots] if r["symbol"] not in new_uni]
    if skipped:
        print(f"  E 高但被「摘除」规则挡掉：{skipped}")

    if not (a.apply and a.lane):
        print("\n  （预览模式）加 --apply --lane mm_asterdex 才写库。")
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
            meta["symbols"] = new_uni
            meta["h123_selector_rollback"] = {"symbols": before}
            meta["universe"] = {
                "fixed": [],
                "ai": new_uni,
                "note": "H123：10 槽全部参与轮询，目标＝已实现每周期净额；"
                        "取消硬编码固定币（原 ASTER,XRP,SOL,DOGE,UNI 不参与评分）",
                "as_of": datetime.now().astimezone().isoformat(),
                "expectancy_bp": {r["symbol"]: r["expectancy_bp"]
                                  for r in ok if r["symbol"] in new_uni},
            }
            ops = list(meta.get("ops_changes") or [])
            ops.append({"by": "h123_selector_v2", "op": "set_symbols",
                        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "before": before, "after": new_uni,
                        "reason": "目标函数改为已实现每周期净额；摘除强平率超阈值的币"})
            meta["ops_changes"] = ops[-40:]
            cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now()"
                        " WHERE lane_id=%s",
                        (json.dumps(meta, ensure_ascii=False, default=str), a.lane))
        conn.commit()
    print(f"\n  ⇒ 已写入 {a.lane}：{before} → {new_uni}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
