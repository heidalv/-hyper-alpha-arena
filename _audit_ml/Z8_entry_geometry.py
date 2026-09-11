# -*- coding: utf-8 -*-
"""Z8：入场侧几何（唯一未被关闭的杠杆）——R 距离 vs 波动率 / 过度延伸 / 择时。

Z7 结论：模式笔的判别特征是**峰值大小**（亏损笔峰值中位 0.63% vs 盈利笔 2.14%），
而不是回吐速度——所有出场侧规则（X4/X5/X6/Y19/Y21/Z5/Z6/Z7）都更差。
因此本轮转向**入场侧几何**，检验三条机制假设：

H1 「R 太宽 / 波动太小」：需要 atr14_1h 走多少小时才能到 1R？若 need_h 很大，
   仓位在有限持有窗口内根本到不了 1R → 峰值天然小 → 回吐成大亏。
   过滤：need_h = R% / atr14_1h% ≤ T。
H2 「追高（过度延伸）」：入场价远高于 EMA20_1h（以 ATR 归一）→ 尖峰追进，随即回落。
   过滤：ext_atr = (entry − ema20_1h)/atr_abs ≤ T。
H3 「1h 超买」：入场时 rsi14_1h 过高 → 同理。过滤：rsi ≤ T。

口径：入场过滤器 = 「不满足条件则不建仓」，反事实为该子集的真实总 USD（尊重真实
出场/部分平仓）。指标一律取**入场前一根已收盘 1h K 线**（无未来函数）。
评估：全量 / mid / long / mid·门放行 四子集 + 逐月 + walk-forward（前 60% 选阈、
后 40% 验证）+ bootstrap 95% CI。
"""
from __future__ import annotations

import os
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from deep_long_freshness import build_bar_features, load_klines, pick, learned_ok_prod  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
SEED = 20260910


def load(days=75):
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        poss = [dict(r._mapping) for r in c.execute(text(f"""
            select id, symbol, side, timeframe_tier, entry_price, close_price, sl_price,
                   size, original_size, peak_pnl_pct, unrealized_pnl, partial_realized_pnl,
                   partial_fee_paid, close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and closed_at >= now() - interval '{int(days)} days'
            order by opened_at
        """)).fetchall()]
    return poss


def ind_arrays(s):
    """入场前一根 1h 的 ema20 / rsi14 / atr14（绝对价）。"""
    n = len(s)
    closes = [r[4] for r in s]
    highs = [r[2] for r in s]
    lows = [r[3] for r in s]
    ema = [None] * n
    k = 2.0 / 21.0
    prev = None
    for i, c in enumerate(closes):
        prev = c if prev is None else c * k + prev * (1 - k)
        ema[i] = prev
    rsi = [None] * n
    gains = losses = None
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        g, l = max(d, 0.0), max(-d, 0.0)
        if gains is None:
            gains, losses = g, l
        else:
            gains = (gains * 13 + g) / 14.0
            losses = (losses * 13 + l) / 14.0
        if i >= 14 and (gains + losses) > 0:
            rsi[i] = 100.0 * gains / (gains + losses)
    atr = [None] * n
    trs = []
    for i in range(n):
        if i == 0:
            trs.append(highs[i] - lows[i])
        else:
            trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                           abs(lows[i] - closes[i - 1])))
    acc = None
    for i, tr in enumerate(trs):
        if i < 14:
            acc = (acc or 0.0) + tr
            if i == 13:
                atr[i] = acc / 14.0
        else:
            acc = (acc * 13 + tr) / 14.0
            atr[i] = acc
    return ema, rsi, atr


def build(days=75):
    poss = load(days)
    h1, d1 = load_klines({p["symbol"] for p in poss})
    feats = {}
    for sym in {p["symbol"] for p in poss}:
        s = pick(h1, sym)
        ds = pick(d1, sym)
        if s is None or ds is None or len(s) < 300 or len(ds) < 70:
            continue
        feats[sym] = (s, build_bar_features(s, ds), ind_arrays(s))

    recs = []
    for p in poss:
        sym = p["symbol"]
        if sym not in feats:
            continue
        s, (reg_arr, pos_arr, chg_arr), (ema, rsi, atr) = feats[sym]
        entry = float(p["entry_price"] or 0)
        sz0 = float(p["original_size"] or p["size"] or 0)
        sl = float(p["sl_price"] or 0)
        if entry <= 0 or sz0 <= 0:
            continue
        ts = int(p["opened_at"].timestamp())
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None or i < 20 or abs(s[i][0] - ts) > 7200:
            continue
        j = i - 1  # 入场前一根已收盘 K 线
        if atr[j] is None or ema[j] is None or rsi[j] is None:
            continue
        atr_pct = atr[j] / s[j][4] * 100.0
        r_pct = abs(entry - sl) / entry * 100.0 if sl > 0 else 0.0
        recs.append({
            "id": p["id"], "symbol": sym, "tier": str(p["timeframe_tier"]),
            "side": str(p["side"]), "entry": entry,
            "notional0": sz0 * entry,
            "base_usd": (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
                         - float(p["partial_fee_paid"] or 0)),
            "db_peak": float(p["peak_pnl_pct"] or 0) * 100.0,
            "r_pct": r_pct, "atr_pct": atr_pct,
            "need_h": (r_pct / atr_pct) if atr_pct > 0 else 999.0,
            "ext_atr": ((entry - ema[j]) / atr[j]) if atr[j] > 0 else 0.0,
            "rsi": rsi[j],
            "allow": learned_ok_prod(reg_arr[j], pos_arr[j], chg_arr[j]),
            "mon": str(p["opened_at"])[:7],
            "reason": str(p["close_reason"] or "")[:26],
        })
    return recs


def med(xs):
    v = sorted(xs)
    return v[len(v) // 2] if v else float("nan")


def main() -> int:
    recs = build(75)
    print(f"样本 n={len(recs)}（近 75 天 mid+long，指标取入场前一根 1h）")

    # ---------- Part A：特征分布（模式 vs 盈利 vs 亏损） ----------
    print("\n===== Part A：入场侧特征分布（中位数）=====")
    print(f"{'分组':<22}{'n':>5}{'R%':>8}{'atr%':>8}{'need_h':>8}{'ext_atr':>9}{'rsi':>7}"
          f"{'峰值%':>8}{'USD':>10}")
    groups = [
        ("全量", recs),
        ("模式笔(峰≥0.5&亏)", [r for r in recs if r["db_peak"] >= 0.5 and r["base_usd"] < 0]),
        ("盈利笔", [r for r in recs if r["base_usd"] > 0]),
        ("亏损笔", [r for r in recs if r["base_usd"] <= 0]),
        ("大亏笔(≤-2%)", [r for r in recs if r["base_usd"] / r["notional0"] * 100 <= -2]),
        ("mid", [r for r in recs if r["tier"] == "mid"]),
        ("long", [r for r in recs if r["tier"] == "long"]),
    ]
    for label, sub in groups:
        if not sub:
            continue
        print(f"{label:<22}{len(sub):>5}{med([r['r_pct'] for r in sub]):>8.2f}"
              f"{med([r['atr_pct'] for r in sub]):>8.2f}"
              f"{med([r['need_h'] for r in sub]):>8.2f}"
              f"{med([r['ext_atr'] for r in sub]):>9.2f}"
              f"{med([r['rsi'] for r in sub]):>7.1f}"
              f"{med([r['db_peak'] for r in sub]):>8.2f}"
              f"{sum(r['base_usd'] for r in sub):>+10.2f}")

    # ---------- Part B：单特征入场过滤网格 ----------
    filters = {
        "need_h ≤": ("need_h", lambda r, t: r["need_h"] <= t, (2.0, 3.0, 4.0, 6.0, 8.0, 12.0)),
        "ext_atr ≤": ("ext_atr", lambda r, t: r["ext_atr"] <= t, (0.5, 1.0, 1.5, 2.0, 3.0)),
        "ext_atr ≥": ("ext_atr", lambda r, t: r["ext_atr"] >= t, (0.0, 0.2, 0.4, 0.6, 0.8)),
        "rsi ≤": ("rsi", lambda r, t: r["rsi"] <= t, (55.0, 60.0, 65.0, 70.0, 75.0)),
        "atr% ≥": ("atr_pct", lambda r, t: r["atr_pct"] >= t, (0.3, 0.5, 0.8, 1.2)),
        "R% ≤": ("r_pct", lambda r, t: r["r_pct"] <= t, (2.0, 3.0, 4.0, 5.0, 6.0)),
    }
    subsets = [
        ("全量", recs),
        ("mid", [r for r in recs if r["tier"] == "mid"]),
        ("long", [r for r in recs if r["tier"] == "long"]),
        ("mid·门放行", [r for r in recs if r["tier"] == "mid" and r["allow"]]),
    ]
    for label, sub in subsets:
        if not sub:
            continue
        base = sum(r["base_usd"] for r in sub)
        bm = defaultdict(float)
        for r in sub:
            bm[r["mon"]] += r["base_usd"]
        bm_s = " ".join(f"{m[5:]}:{v:+.0f}" for m, v in sorted(bm.items()))
        print(f"\n=== {label} n={len(sub)} 基线=${base:+.2f} ({bm_s}) ===")
        print(f"{'过滤条件':<20}{'保留n':>7}{'总USD':>10}{'均值%':>9}{'胜率':>7}"
              f"{'模式率':>8}{'≤-2%':>7}  逐月USD")
        for name, (_key, fn, ths) in filters.items():
            for t in ths:
                kept = [r for r in sub if fn(r, t)]
                if len(kept) < 8:
                    continue
                usds = [r["base_usd"] for r in kept]
                pcts = [u / r["notional0"] * 100 for u, r in zip(usds, kept)]
                pat = sum(1 for r in kept if r["db_peak"] >= 0.5 and r["base_usd"] < 0)
                km = defaultdict(float)
                for u, r in zip(usds, kept):
                    km[r["mon"]] += u
                km_s = " ".join(f"{m[5:]}:{v:+.0f}" for m, v in sorted(km.items()))
                print(f"{name+f' {t:g}':<20}{len(kept):>7}{sum(usds):>+10.2f}"
                      f"{sum(pcts)/len(pcts):>+9.3f}"
                      f"{sum(1 for x in pcts if x > 0)/len(pcts):>7.3f}"
                      f"{pat/len(kept):>8.3f}{sum(1 for x in pcts if x <= -2):>7}  {km_s}")

    # ---------- Part C：walk-forward + bootstrap ----------
    print("\n===== Part C：walk-forward（前 60% 选阈 → 后 40% 验证）=====")
    for label, sub in subsets:
        if len(sub) < 30:
            print(f"\n--- {label} n={len(sub)} 样本不足 ---")
            continue
        cut = int(len(sub) * 0.6)
        tr, te = sub[:cut], sub[cut:]
        best, best_usd = None, None
        for name, (_k, fn, ths) in filters.items():
            for t in ths:
                kept = [r for r in tr if fn(r, t)]
                if len(kept) < 8:
                    continue
                u = sum(r["base_usd"] for r in kept)
                if best_usd is None or u > best_usd:
                    best, best_usd = (name, t, fn), u
        if best is None:
            continue
        name, t, fn = best
        tr_base = sum(r["base_usd"] for r in tr)
        te_base = sum(r["base_usd"] for r in te)
        kept_te = [r for r in te if fn(r, t)]
        te_var = sum(r["base_usd"] for r in kept_te)
        print(f"\n--- {label} 训练n={len(tr)} 验证n={len(te)} ---")
        print(f"  训练最优={name} {t:g}（训练 实际${tr_base:+.2f} → 保留{len([r for r in tr if fn(r,t)])}笔 ${best_usd:+.2f}）")
        print(f"  验证 实际${te_base:+.2f} → 过滤后 {len(kept_te)}笔 ${te_var:+.2f}（差 ${te_var-te_base:+.2f}）")
        diffs = [-r["base_usd"] / r["notional0"] * 100 for r in te if not fn(r, t)]
        rnd = random.Random(SEED)
        n = len(diffs)
        if n == 0:
            print("  （验证集未被过滤任何笔）")
            continue
        boots = sorted(sum(diffs[rnd.randrange(n)] for _ in range(n)) / n for _ in range(4000))
        obs = sum(diffs) / n
        lo, hi = boots[int(0.025 * 4000)], boots[int(0.975 * 4000)]
        print(f"  被过滤 {n} 笔的平均 pct={obs:+.4f}% CI[{lo:+.4f},{hi:+.4f}] "
              f"（{'显著' if lo > 0 or hi < 0 else '不显著(跨0)'}：若 CI 全>0 则过滤有效）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
