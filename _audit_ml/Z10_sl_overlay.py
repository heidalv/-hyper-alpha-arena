# -*- coding: utf-8 -*-
"""Z10：在**真实出场路径之上**追加止损（唯一没被正确测过的风险几何杠杆）。

方法论纠正（本轮发现）：
- Z8 的「R% ≤ 4」看起来极好（+181 vs +36、0 笔 ≤-2%），但那是**未来函数**：
  `paper_positions.sl_price` 是**活体字段**（追踪/保本会上移），
  Z9b 实测 mid 层 172/229 笔的 R% 恰好 = 4.5%、long 层 34/54 恰好 = 6.5%（初始固定 SL），
  而「R% < 2%」的 22 笔胜率 **100%** —— 正是被上移到成本区的那批。该结论作废。
- Z5 的「收紧 SL」测的是**纯机械 SL**（把主动出场全部替换掉），因此同时删掉了
  thesis_invalidation 等正贡献通道 → 必然更差，**不是对 SL 宽度本身的检验**。

本轮正确口径：**保留真实出场路径**，只在真实出场之前**追加**一条硬止损
（1h 低点触及即按 SL 价成交 + 滑点）。另测「无动能才收紧」（traction-conditional）：
峰值 < P 时把 SL 收到 X%，峰值达标则维持原样（P 在实时可见，无未来函数）。
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

from deep_long_freshness import build_bar_features, load_klines, learned_ok_prod  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
FEE_SIDE = 0.0005
SLIP = 0.0005
SEED = 20260910


def pick(series, sym, ts):
    """按时间覆盖挑序列（默认 pick 只按长度，可能挑到过期序列）。"""
    for ex in ("asterdex", "binance"):
        v = series.get((ex, sym))
        if v and len(v) > 200 and v[0][0] <= ts <= v[-1][0] + 86400:
            return v
    return None


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
        evs = [dict(r._mapping) for r in c.execute(text("""
            select position_id, quantity, price, created_at
            from position_exit_events
            where event_type='partial_exit_event' and created_at >= now() - interval '95 days'
            order by position_id, created_at
        """)).fetchall()]
    return poss, evs


def build(days=75):
    poss, evs = load(days)
    by_pos = defaultdict(list)
    for e in evs:
        if e["quantity"] and e["price"]:
            by_pos[e["position_id"]].append(e)
    h1, d1 = load_klines({p["symbol"] for p in poss})
    recs = []
    for p in poss:
        sym = p["symbol"]
        s = pick(h1, sym, int(p["opened_at"].timestamp()))
        if not s:
            continue
        entry = float(p["entry_price"] or 0)
        close = float(p["close_price"] or 0)
        sz0 = float(p["original_size"] or p["size"] or 0)
        if entry <= 0 or close <= 0 or sz0 <= 0:
            continue
        ts = int(p["opened_at"].timestamp())
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None or i == 0 or abs(s[i][0] - ts) > 7200:
            continue
        # 门判定（learned_ok_prod，与生产同口径；用入场前一根日线/1h 特征）
        allow = True
        try:
            ds = d1.get(("asterdex", sym)) or d1.get(("binance", sym))
            if ds and len(ds) >= 70 and len(s) >= 300:
                _reg, _pos, _chg = build_bar_features(s, ds)
                allow = bool(learned_ok_prod(_reg[i], _pos[i], _chg[i]))
        except Exception:
            allow = True
        recs.append({
            "id": p["id"], "symbol": sym, "tier": str(p["timeframe_tier"]),
            "side": str(p["side"]), "entry": entry, "close": close, "sz0": sz0,
            "notional0": sz0 * entry,
            "base_usd": (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
                         - float(p["partial_fee_paid"] or 0)),
            "db_peak": float(p["peak_pnl_pct"] or 0) * 100.0,
            "s": s, "i": i,
            "close_ts": int(p["closed_at"].timestamp()) if p["closed_at"] else s[-1][0],
            "parts": sorted([(int(e["created_at"].timestamp()), float(e["quantity"]),
                              float(e["price"])) for e in by_pos.get(p["id"], [])],
                            key=lambda x: x[0]),
            "mon": str(p["opened_at"])[:7],
            "reason": str(p["close_reason"] or "")[:26],
            "allow": allow,
        })
    return recs


def overlay_sl(r, sl_pct, peak_gate=None, sl_if_no_traction=None):
    """真实路径 + 追加硬止损。peak_gate=None → 恒定 sl_pct；否则峰值<P 时用 sl_if_no_traction。"""
    sign = 1.0 if r["side"] == "long" else -1.0
    s, i0 = r["s"], r["i"]
    parts, pi = r["parts"], 0
    realized, fees, qty = 0.0, 0.0, r["sz0"]
    peak = 0.0
    cur = sl_pct
    for k in range(i0, len(s)):
        ts, _o, h, l, c = s[k]
        while pi < len(parts) and parts[pi][0] <= ts:
            _t, q, px = parts[pi]
            q = min(q, qty)
            realized += q * sign * (px - r["entry"])
            fees += q * px * (FEE_SIDE + SLIP) * 2
            qty -= q
            pi += 1
        if qty <= 1e-12:
            break
        if peak_gate is not None and peak < peak_gate:
            cur = sl_if_no_traction
        else:
            cur = sl_pct
        sl_price = r["entry"] * (1 - sign * cur / 100.0)
        hit = (l <= sl_price) if sign > 0 else (h >= sl_price)
        if hit:
            fill = sl_price * (1 - sign * SLIP)
            realized += qty * sign * (fill - r["entry"])
            fees += qty * fill * (FEE_SIDE + SLIP) * 2
            return realized - fees, True
        hi = sign * (h - r["entry"]) / r["entry"] * 100
        peak = max(peak, hi)
        if ts >= r["close_ts"]:
            break
    return r["base_usd"], False


def table(label, sub, variants):
    print(f"\n=== {label} n={len(sub)} 基线=${sum(r['base_usd'] for r in sub):+.2f} ===")
    print(f"{'方案':<26}{'总USD':>10}{'均值%':>9}{'胜率':>7}{'≤-2%':>7}{'触发':>6}  逐月USD")
    for name, args in [("实际（无追加SL）", None)] + variants:
        if args is None:
            usds, trig = [r["base_usd"] for r in sub], 0
        else:
            res = [overlay_sl(r, *args) for r in sub]
            usds = [x[0] for x in res]
            trig = sum(1 for x in res if x[1])
        pcts = [u / r["notional0"] * 100 for u, r in zip(usds, sub)]
        bym = defaultdict(float)
        for u, r in zip(usds, sub):
            bym[r["mon"]] += u
        bym_s = " ".join(f"{m[5:]}:{v:+.0f}" for m, v in sorted(bym.items()))
        print(f"{name:<26}{sum(usds):>+10.2f}{sum(pcts)/len(pcts):>+9.3f}"
              f"{sum(1 for x in pcts if x > 0)/len(pcts):>7.3f}"
              f"{sum(1 for x in pcts if x <= -2):>7}{trig:>6}  {bym_s}")


def main() -> int:
    recs = build(75)
    print(f"样本 n={len(recs)}（近 75 天，追加 SL 叠加在真实出场路径上）")
    fixed = [(f"追加SL {x}%", (x,)) for x in (1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.5)]
    cond = []
    for p in (0.3, 0.5, 1.0):
        for x in (2.0, 2.5, 3.0):
            cond.append((f"峰<{p}% → SL{x}%", (99.0, p, x)))
    subsets = [
        ("全量", recs),
        ("mid", [r for r in recs if r["tier"] == "mid"]),
        ("long", [r for r in recs if r["tier"] == "long"]),
    ]
    for label, sub in subsets:
        if sub:
            table(label, sub, fixed + cond)

    # 最优候选 bootstrap（全量）
    print("\n===== bootstrap：候选 vs 实际（全量，pct 差）=====")
    cands = fixed + cond
    best = None
    for name, args in cands:
        u = sum(overlay_sl(r, *args)[0] for r in recs)
        if best is None or u > best[1]:
            best = ((name, args), u)
    print(f"  全量最优={best[0][0]} → ${best[1]:+.2f}（基线 ${sum(r['base_usd'] for r in recs):+.2f}）")
    diffs = [overlay_sl(r, *best[0][1])[0] / r["notional0"] * 100 - r["base_usd"] / r["notional0"] * 100
             for r in recs]
    rnd = random.Random(SEED)
    n = len(diffs)
    boots = sorted(sum(diffs[rnd.randrange(n)] for _ in range(n)) / n for _ in range(4000))
    obs = sum(diffs) / n
    lo, hi = boots[int(0.025 * 4000)], boots[int(0.975 * 4000)]
    print(f"  观测差={obs:+.4f}% CI[{lo:+.4f},{hi:+.4f}] "
          f"{'显著' if lo > 0 or hi < 0 else '不显著(跨0)'}")

    # walk-forward：前 60% 选参 → 后 40% 验证
    print("\n===== walk-forward（前 60% 选参 → 后 40% 验证）=====")
    for label, sub in subsets:
        if len(sub) < 40:
            continue
        cut = int(len(sub) * 0.6)
        tr, te = sub[:cut], sub[cut:]
        bb = None
        for name, args in cands:
            u = sum(overlay_sl(r, *args)[0] for r in tr)
            if bb is None or u > bb[1]:
                bb = ((name, args), u)
        name, args = bb[0]
        trb = sum(r["base_usd"] for r in tr)
        teb = sum(r["base_usd"] for r in te)
        tev = sum(overlay_sl(r, *args)[0] for r in te)
        ntrig = sum(1 for r in te if overlay_sl(r, *args)[1])
        print(f"\n--- {label} 训练n={len(tr)} 验证n={len(te)} ---")
        print(f"  训练最优={name}（训练 实际${trb:+.2f} → ${bb[1]:+.2f}）")
        print(f"  验证 实际${teb:+.2f} → ${tev:+.2f}（差 ${tev-teb:+.2f}，触发 {ntrig} 笔）")
        diffs = [overlay_sl(r, *args)[0] / r["notional0"] * 100 - r["base_usd"] / r["notional0"] * 100
                 for r in te]
        rnd = random.Random(SEED)
        n = len(diffs)
        boots = sorted(sum(diffs[rnd.randrange(n)] for _ in range(n)) / n for _ in range(4000))
        obs = sum(diffs) / n
        lo, hi = boots[int(0.025 * 4000)], boots[int(0.975 * 4000)]
        print(f"  bootstrap 观测差={obs:+.4f}% CI[{lo:+.4f},{hi:+.4f}] "
              f"{'显著' if lo > 0 or hi < 0 else '不显著(跨0)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
