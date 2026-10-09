# -*- coding: utf-8 -*-
"""H470 `ofi_flatten_taker` 出场的反事实检验（决定性：保留还是回滚 h441）。

背景（h469）：09-28 13:17:12Z 重启后，出场路径被 ofi_flatten_taker 主导
（6h 内 98/116 笔），往返中位 76s、硬顶归零 —— 但每腿手续费从 ≈0 升到 −1.4bp，
净/腿稳定在 −3bp。h441（`ofi_flatten_threshold` 0→0.5 @13:13Z）是这一路径的开关。

反事实口径（逐笔 taker 平仓腿）：
  设出场腿已实现净额 `net_bp`（相对开仓均价的往返 P&L，含费）、
  该腿手续费 `fee_bp`（taker 为负）、出场后 300s 中价 `m`、出场基价 `base`：
    · 若**继续持有到 T+300s 再以中价被动出场**：价格项多赚 `(m−p)/p`，
      手续费从 taker 变为 maker(0%) ⇒ 省下 `|fee_bp|`。
    ⇒ `net_alt = net_bp + (m−p)/p*1e4 + |fee_bp|`
  再定义"离场方向有利漂移" `d_post = sign*(m−base)/base*1e4`
  （sell 出场 sign=−1，buy 出场 sign=+1；`d_post>0` ⇒ 出场正确、继续朝离场方向走）。
  ⇒ 等价地 `net_alt = net_bp − d_post + |fee_bp|`。

裁决：
  · `mean(net_alt) − mean(net_bp) > 0` 且 t 显著 ⇒ **回滚**（平仓在毁值：既付 taker 费又过早离场）；
  · < 0 且显著 ⇒ 保留（平仓在避免更大损失）；
  · 不显著 ⇒ 中性（但可单看"省手续费"一项是否有正贡献）。

只读。
"""
import json
import math
import pathlib
import sys

import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOURS = 14
PATH_MATCH = "ofi_flatten_taker"

import argparse  # noqa: E402

_ap = argparse.ArgumentParser(description="出场腿反事实检验（可指定路径）")
_ap.add_argument("--path", default=PATH_MATCH, help="exit_path（精确匹配）")
_ap.add_argument("--hours", type=float, default=HOURS)
_a = _ap.parse_args()
PATH_MATCH = _a.path
HOURS = _a.hours

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("""
    SELECT ts, symbol, meta_json->>'side', net_bp::float8,
           COALESCE(fee_bp, 0)::float8, COALESCE((meta_json->>'qty')::float8, 0)
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '%s hours'
      AND meta_json->>'exit_path' = %s
    ORDER BY ts
""" % (HOURS, "'" + PATH_MATCH + "'"))
legs = cur.fetchall()
print(f"近 {HOURS}h `{PATH_MATCH}` 出场 {len(legs)} 笔")
if not legs:
    raise SystemExit(1)

c2 = psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market"),
                     autocommit=True)
cur2 = c2.cursor()
rows = []
for ts, sym, side, net, fee, qty in legs:
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
    sign = -1.0 if side == "sell" else 1.0
    k = min(ks, key=lambda x: abs(x - (t0 + 300)))
    d_post = sign * (g[k] - base) / base * 1e4
    net_alt = float(net or 0.0) - d_post + abs(float(fee or 0.0))
    rows.append({"ts": ts.isoformat(), "sym": sym, "side": side,
                 "net_bp": float(net or 0.0), "fee_bp": float(fee or 0.0),
                 "d_post300": d_post, "net_alt": net_alt,
                 "fee_saved": abs(float(fee or 0.0))})


def st(xs):
    n = len(xs)
    if n < 5:
        return None
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    sd = math.sqrt(var)
    return {"n": n, "mean": round(m, 3),
            "t": round(m / (sd / math.sqrt(n)), 2) if sd > 0 else 0.0}


nets = [r["net_bp"] for r in rows]
alts = [r["net_alt"] for r in rows]
diffs = [r["net_alt"] - r["net_bp"] for r in rows]
fees = [r["fee_saved"] for r in rows]
dps = [r["d_post300"] for r in rows]

print("\n== 已实现（现状） ==")
for lab, v in (("net_bp", nets), ("手续费(绝对值)", fees), ("出场后有利漂移 d_post300", dps),
               ("反事实 net_alt（持有到 +300s 被动出场）", alts),
               ("差值 net_alt − net_bp", diffs)):
    s = st(v)
    print(f"  {lab:34s} " + (f"n={s['n']} 均={s['mean']:+.2f}bp t={s['t']:+.2f}"
                             if s else "样本不足"))

# 按币分组看一致性
print("\n== 分币 ==")
by: dict = {}
for r in rows:
    by.setdefault(r["sym"], []).append(r)
for s_, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
    m_net = sum(x["net_bp"] for x in v) / len(v)
    m_alt = sum(x["net_alt"] for x in v) / len(v)
    print(f"  {s_:5s} n={len(v):4d} net={m_net:+7.2f}  net_alt={m_alt:+7.2f}  "
          f"Δ={m_alt-m_net:+7.2f}bp")

sd = st(diffs)
if sd and sd["t"] > 1.5 and sd["mean"] > 0:
    verdict = (f"回滚：taker 平仓在毁值 —— 反事实持有到 +300s 被动出场每笔多 "
               f"{sd['mean']:+.2f}bp（t={sd['t']:+.2f}）")
elif sd and sd["t"] < -1.5:
    verdict = (f"保留：平仓在避免更大损失（反事实每笔 {sd['mean']:+.2f}bp，"
               f"t={sd['t']:+.2f}）")
else:
    verdict = (f"中性/不显著（Δ={sd['mean']:+.2f}bp t={sd['t']:+.2f}）："
               f"价格维度无优势，但 taker 费 {st(fees)['mean']:+.2f}bp/笔是确定性支出 ⇒ "
               f"倾向回滚（除非有尾部保护证据）" if sd else "样本不足")
print(f"\n⇒ 裁决：{verdict}")

out = ROOT / "research_l1" / "out" / f"h470_cf_{PATH_MATCH}.json"
out.write_text(json.dumps({
    "path": PATH_MATCH, "hours": HOURS, "n": len(rows),
    "net_bp": st(nets), "net_alt": st(alts), "diff": st(diffs),
    "fee_saved": st(fees), "d_post300": st(dps), "verdict": verdict,
    "legs": rows[:400]}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"已存 {out.relative_to(ROOT)}")
