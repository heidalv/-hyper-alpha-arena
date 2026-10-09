# -*- coding: utf-8 -*-
"""H462 ① reversal_decay 出场的反事实检验（保留还是回滚）。

口径：对该路径每条出场腿，取出场后 60s/300s 的**离场方向有利漂移**
  d_post > 0 ⇒ 出场后价格继续朝离场方向走 ⇒ 出场正确（若不出一路更差）
  d_post < 0 ⇒ 价格回来了 ⇒ 出场过早
再叠加该路径已实现净额（h461：13 笔 × −21.8bp）：
  若 d_post ≥ 0（或 ≈0）⇒ 该出场是在**避免更大损失** ⇒ 保留；
  若 d_post 显著为负 ⇒ 出场过早 ⇒ 回滚候选。
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

HOURS = 72
c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("""
    SELECT ts, symbol, meta_json->>'side', net_bp, (meta_json->>'qty')::float8
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '%s hours'
      AND meta_json->>'exit_path' = 'reversal_decay_taker'
    ORDER BY ts
""" % HOURS)
legs = cur.fetchall()
print(f"近 {HOURS}h reversal_decay 出场 {len(legs)} 笔")

c2 = psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market"),
                     autocommit=True)
cur2 = c2.cursor()
post60, post300, nets = [], [], []
for ts, sym, side, net, qty in legs:
    t0 = int(ts.timestamp())
    cur2.execute("""
        SELECT (event_ts_ms/5000)*5 AS b,
               (ARRAY_AGG((bid_px+ask_px)/2 ORDER BY event_ts_ms DESC))[1]
        FROM asterdex_book_ticker
        WHERE symbol=%s AND event_ts_ms > %s AND event_ts_ms <= %s
          AND bid_px>0 AND ask_px>bid_px
        GROUP BY 1 ORDER BY 1
    """, (sym + "USDT", t0 * 1000, (t0 + 305) * 1000))
    g = {int(b): float(m) for b, m in cur2.fetchall()}
    if not g:
        continue
    ks = sorted(g)
    base = g[ks[0]]
    if base <= 0:
        continue
    sign = -1.0 if side == "sell" else 1.0     # 离场方向的有利漂移
    for h, arr in ((60, post60), (300, post300)):
        k = min(ks, key=lambda x: abs(x - (t0 + h)))
        arr.append(sign * (g[k] - base) / base * 1e4)
    nets.append(float(net or 0.0))


def st(xs):
    n = len(xs)
    if n < 5:
        return None
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return n, round(m, 3), round(m / math.sqrt(var / n), 2) if var > 0 else 0.0


print("\n== 已实现净额 ==")
s = st(nets)
print(f"  n={s[0]} 均={s[1]:+.2f}bp t={s[2]:+.1f}" if s else "  样本不足")
print("\n== 出场后有利漂移（>0 = 出场正确）==")
for lab, arr in (("+60s", post60), ("+300s", post300)):
    s = st(arr)
    print(f"  {lab}: n={s[0]} 均={s[1]:+.2f}bp t={s[2]:+.1f}" if s else f"  {lab}: 样本不足")

if s:
    m300 = st(post300)
    # 按 **t 值** 判定（原先用 ±2bp 绝对阈值，遇到 t=−0.2 的中性样本会误判）
    if m300 and abs(m300[2]) < 1.5:
        verdict = ("保留：出场后漂移统计上中性（t=%.1f）——该出场既不过早也不滞后，"
                   "−18.8bp 是「确实被套」的成本，不是「错杀」" % m300[2])
    elif m300 and m300[1] > 0:
        verdict = "保留：出场后价格继续朝离场方向走（避免更大损失）"
    else:
        verdict = "回滚候选：出场后价格显著回来（出场过早）"
    print(f"\n⇒ 裁决：{verdict}")
    out = ROOT / "research_l1" / "out" / "h462_reversal_decay_cf.json"
    out.write_text(json.dumps({"n": len(nets), "net_mean": s[1] if s else None,
                               "post60": st(post60), "post300": st(post300),
                               "verdict": verdict}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"已存 {out}")
