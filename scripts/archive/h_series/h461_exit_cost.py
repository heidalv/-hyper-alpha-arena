# -*- coding: utf-8 -*-
"""H461 出场侧成本分解（实测口径）：出场腿的持有时间 × 状态 × 捕获 → 最优出场时机。

样本：近 24h 账本。用库存回放把每条"减仓腿"（平仓）与其"入场腿"配对：
  · 配对 = 平仓腿之前最近的同币反向腿（FIFO 近似）
  · 持有时间 = 平仓时刻 − 入场时刻
  · 捕获 = 该腿 spread_bp（相对中价的成交优势）；净 = net_bp
输出：
  1) 被动出场（round）按持有时间分桶的净额（找最优持有窗口）
  2) 强平出场（stop/jump/hard/ofi_flatten/trail）按路径的净额与占比
  3) 出场腿捕获分布（是否贴中价成交）
只读。
"""
import sys
import json
import math
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOURS = 24
c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("""
    SELECT ts, symbol, meta_json->>'side', (meta_json->>'qty')::float8,
           COALESCE(NULLIF(meta_json->>'exit_path',''),'round') AS ep,
           spread_bp, net_bp
    FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '%s hours'
    ORDER BY ts
""" % HOURS)
rows = cur.fetchall()
print(f"近 {HOURS}h 成交 {len(rows)} 笔")

# 库存回放配对：每笔减仓腿配最近的反向入场腿
by_sym = {}
for r in rows:
    by_sym.setdefault(r[1], []).append(r)

pairs = []   # (hold_sec, exit_path, capture_bp, net_bp, symbol)
for sym, fills in by_sym.items():
    inv = 0.0
    open_lots = []      # [(ts, side)]
    for ts, s, side, qty, ep, sp, net in fills:
        d = qty if side == "buy" else -qty
        prev = inv
        inv += d
        same_dir = (prev >= 0) == (d >= 0)
        if prev == 0 or same_dir:
            open_lots.append((ts, side))
        else:
            if open_lots:
                t0, s0 = open_lots.pop(0)
                hold = (ts - t0).total_seconds()
                pairs.append((hold, ep, float(sp or 0.0), float(net or 0.0), sym))
print(f"可配对出场腿 {len(pairs)}")


def stat(xs):
    n = len(xs)
    if n < 10:
        return None
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return n, round(m, 3), round(m / math.sqrt(var / n), 2) if var > 0 else 0.0


print("\n== ① 被动出场（round）按持有时间分桶 ==")
rnd = [p for p in pairs if p[1] == "round"]
print(f"  被动出场 {len(rnd)} 笔（占 {len(rnd)/max(len(pairs),1)*100:.0f}%）")
BUCKETS = ((0, 30), (30, 60), (60, 120), (120, 180), (180, 300), (300, 600), (600, 99999))
for lo, hi in BUCKETS:
    sel = [p[3] for p in rnd if lo <= p[0] < hi]
    st = stat(sel)
    if st:
        print(f"  持有 {lo:>4}-{hi if hi < 99999 else '∞':<5}s  n={st[0]:>4}  净={st[1]:>+7.3f}bp  t={st[2]:>+5.1f}")
    else:
        print(f"  持有 {lo:>4}-{hi if hi < 99999 else '∞':<5}s  （样本不足）")

print("\n== ② 各出场路径净额与占比 ==")
paths = {}
for p in pairs:
    paths.setdefault(p[1], []).append(p[3])
for k, v in sorted(paths.items(), key=lambda x: -len(x[1])):
    st = stat(v)
    if st:
        print(f"  {k:<24} n={st[0]:>4}（{st[0]/len(pairs)*100:>4.0f}%）  净={st[1]:>+8.3f}bp  t={st[2]:>+5.1f}")

print("\n== ③ 出场腿捕获（spread_bp，正=优于中价）==")
for k in ("round", "stop_loss_taker", "timeout_hard_taker", "ofi_flatten_taker"):
    sel = [p[2] for p in pairs if p[1] == k]
    st = stat(sel)
    if st:
        print(f"  {k:<24} n={st[0]:>4}  捕获={st[1]:>+7.3f}bp  t={st[2]:>+5.1f}")

# 最优持有窗口（被动出场）
best = None
for lo, hi in BUCKETS:
    sel = [p[3] for p in rnd if lo <= p[0] < hi]
    st = stat(sel)
    if st and (best is None or st[1] > best[1]):
        best = (f"{lo}-{hi}s", st[1], st[0])
if best:
    print(f"\n⇒ 被动出场最优持有窗口：{best[0]}（净 {best[1]:+.3f}bp，n={best[2]}）")

out = ROOT / "research_l1" / "out" / "h461_exit_cost.json"
out.write_text(json.dumps({"n_pairs": len(pairs),
                           "by_path": {k: {"n": len(v), "mean": round(sum(v)/len(v), 3)}
                                       for k, v in paths.items()},
                           "best_hold_window": best}, ensure_ascii=False, indent=2),
               encoding="utf-8")
print(f"已存 {out}")
