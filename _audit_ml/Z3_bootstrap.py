# -*- coding: utf-8 -*-
"""门差异的 bootstrap 显著性检验（Z3）。

Z2 发现门的价值集中在 8 月，7/9 月反而拦掉盈利。本脚本用 bootstrap 给
「放行集均值 − 拦截集均值」与「大亏率差」一个置信区间：
  - 若 95% CI 跨 0 → 门对总收益无统计支撑（只能说"不排除无效"）；
  - 大亏率差同理（尾部风险的收益是否稳定）。

分层：mid / long；窗口：全期（7–9月）与逐月。
"""
from __future__ import annotations

import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from deep_long_freshness import build_bar_features, load_klines, pick, learned_ok_prod  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
N_BOOT = 4000
random.seed(20260910)


def boot_ci(values, n_boot=N_BOOT, q=(0.05, 0.95)):
    if not values:
        return None
    n = len(values)
    means = []
    for _ in range(n_boot):
        s = sum(values[random.randrange(n)] for _ in range(n)) / n
        means.append(s)
    means.sort()
    return means[int(q[0] * n_boot)], means[min(n_boot - 1, int(q[1] * n_boot))], sum(values) / n


def main() -> int:
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        poss = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, timeframe_tier, entry_price, original_size, size,
                   peak_pnl_pct, unrealized_pnl, partial_realized_pnl, partial_fee_paid,
                   opened_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and opened_at >= '2026-07-01'
            order by opened_at
        """)).fetchall()]
    h1, d1 = load_klines({p["symbol"] for p in poss})
    feats = {}
    for sym in {p["symbol"] for p in poss}:
        s = pick(h1, sym)
        ds = pick(d1, sym)
        if s is None or ds is None or len(s) < 300 or len(ds) < 70:
            continue
        feats[sym] = (s, build_bar_features(s, ds))

    rows = []
    for p in poss:
        sym = p["symbol"]
        if sym not in feats:
            continue
        s, (reg_arr, pos_arr, chg_arr) = feats[sym]
        entry = float(p["entry_price"] or 0)
        sz0 = float(p["original_size"] or p["size"] or 0)
        if entry <= 0 or sz0 <= 0:
            continue
        ts = int(p["opened_at"].timestamp())
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None or i == 0 or abs(s[i][0] - ts) > 7200:
            continue
        usd = (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
               - float(p["partial_fee_paid"] or 0))
        rows.append({
            "mon": str(p["opened_at"])[:7], "tier": str(p["timeframe_tier"]),
            "pct": usd / (entry * sz0) * 100,
            "big": 1.0 if usd / (entry * sz0) * 100 <= -2 else 0.0,
            "allow": learned_ok_prod(reg_arr[i], pos_arr[i], chg_arr[i]),
        })

    def report(label, sub):
        a = [r for r in sub if r["allow"]]
        b = [r for r in sub if not r["allow"]]
        if not a or not b:
            print(f"  {label:<22} 样本不足 (放行{len(a)}/拦截{len(b)})")
            return
        # 均值差（放行 − 拦截）bootstrap：分别重采样再作差
        n_a, n_b = len(a), len(b)
        diffs, diffs_big = [], []
        for _ in range(N_BOOT):
            ma = sum(a[random.randrange(n_a)]["pct"] for _ in range(n_a)) / n_a
            mb = sum(b[random.randrange(n_b)]["pct"] for _ in range(n_b)) / n_b
            diffs.append(ma - mb)
            ba = sum(a[random.randrange(n_a)]["big"] for _ in range(n_a)) / n_a
            bb = sum(b[random.randrange(n_b)]["big"] for _ in range(n_b)) / n_b
            diffs_big.append(ba - bb)
        diffs.sort()
        diffs_big.sort()
        lo, hi = diffs[int(0.025 * N_BOOT)], diffs[int(0.975 * N_BOOT)]
        blo, bhi = diffs_big[int(0.025 * N_BOOT)], diffs_big[int(0.975 * N_BOOT)]
        obs = sum(r["pct"] for r in a) / n_a - sum(r["pct"] for r in b) / n_b
        obs_big = sum(r["big"] for r in a) / n_a - sum(r["big"] for r in b) / n_b
        sig = "显著" if lo > 0 or hi < 0 else "不显著(跨0)"
        sig_big = "显著" if blo > 0 or bhi < 0 else "不显著(跨0)"
        print(f"  {label:<22} 放行n={n_a:>3} 拦截n={n_b:>3} "
              f"均值差={obs:>+7.3f}% CI[{lo:>+7.3f},{hi:>+7.3f}] {sig:<12} "
              f"大亏率差={obs_big:>+6.3f} CI[{blo:>+6.3f},{bhi:>+6.3f}] {sig_big}")

    for tier in ("mid", "long"):
        sub_all = [r for r in rows if r["tier"] == tier]
        print(f"\n===== tier={tier} =====")
        report("全期 7–9月", sub_all)
        for mon in sorted({r["mon"] for r in sub_all}):
            report(mon, [r for r in sub_all if r["mon"] == mon])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
