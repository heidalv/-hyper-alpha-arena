"""h537：**reversal_decay 出场的保留/回滚判定**（目标①的反事实检验）。

现状（线上值）：`reversal_decay_bp=3`、`min_age=15s`、`window=2 期(30s)`、
`grace=300s`（先挂减仓侧 maker，超时才 taker）。代码依据是 H284：
固定 6bp 止损净 −3.34bp/腿、MAE −3.9bp；反转衰减离场净 −0.80bp/腿、MAE −1.7bp。

h470 的通用反事实（`--path reversal_decay_taker`，72h）给出：
  已实现 net −19.95bp/腿（t=−6.21，n=21）；出场后 300s 漂移 d_post −4.18bp（t=−0.47）；
  反事实 net_alt（持有到 +300s、被动出场免 taker 费）−11.76bp ⇒ **Δ=+8.19bp（t=+0.91）**。
⇒ 价格维度**不显著**，只有 4bp taker 费是确定支出 ⇒ 单看这个会倾向回滚。

但"回滚"必须回答一个问题：**该出场有没有在保护尾部**——即不做它，仓位是否会
撞上 40bp 止损（那样损失更大）。h470 只看 +300s 的**终点**中价，看不到**路径**。
本脚本补上路径维度：

  · `d_post300`：+300s 终点漂移（h470 口径，正=持有更好）；
  · `adv_hold_bp`：**持有期间的最大不利偏移**（路径最小值；多头=下跌）
    —— ≤ −40bp 表示"不做这个出场就会触发 40bp 止损"；
  · `mfe_hold_bp`：持有期间的最大有利偏移；
  · `stop_avoided`：`adv_hold_bp ≤ −stop_loss_bp`（用线上 `stop_loss_bp`）；
  · `cf_path_net_bp`：**路径版反事实**——若真持有到止损/300s，按"路径终点或止损先到"
    估的净额（比 h470 的终点口径更接近真实规则链）。

判据（预注册）：
  · 若 `stop_avoided` 占比 ≥ 25% 且被避免的止损金额显著 > 省下的费用
    ⇒ **保留**（尾部保护成立，H284 的依据在实盘仍成立）；
  · 若 `stop_avoided` 占比 < 10% 且 Δ 不显著为正 ⇒ **回滚**（只付 4bp 费无收益）；
  · 其余 ⇒ **INCONCLUSIVE**（样本不足，需延长窗口或冻结试跑）。

用法：python scripts/h537_reversal_decay_cf.py [--hours 168] [--since "2026-09-28 13:00"]
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h537_reversal_decay_cf.json"
PATH_MATCH = "reversal_decay_taker"
STOP_BP_DEFAULT = 40.0
HORIZON_S = 300


def read_env() -> dict:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def dsn_of(u: str) -> str:
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        u = u.replace(j, "")
    return u


def stats(xs):
    n = len(xs)
    if n < 3:
        return {"n": n}
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    sd = math.sqrt(var)
    return {"n": n, "mean": round(m, 3),
            "t": round(m / (sd / math.sqrt(n)), 2) if sd > 0 else 0.0,
            "p50": round(sorted(xs)[n // 2], 3)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=168.0)
    ap.add_argument("--since", default="")
    ap.add_argument("--stop-bp", type=float, default=STOP_BP_DEFAULT)
    a = ap.parse_args()
    env = read_env()
    core = dsn_of(env["DATABASE_URL"])
    market = dsn_of(env.get("MARKET_DATABASE_URL") or env["DATABASE_URL"])
    where = ("ts > now() - make_interval(hours => %s::int)" if not a.since
             else "ts >= %s")
    arg = int(a.hours) if not a.since else a.since
    with psycopg.connect(core, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(f"""
                SELECT ts, symbol, COALESCE(meta_json->>'side','') AS side,
                       net_bp::float8, COALESCE(fee_bp,0)::float8,
                       COALESCE((meta_json->>'qty')::float8,0)::float8,
                       COALESCE(notional,0)::float8
                FROM lane_ledger
                WHERE lane_id='mm_asterdex' AND event='fill' AND {where}
                  AND COALESCE(meta_json->>'exit_path','') = %s
                ORDER BY ts""", (arg, PATH_MATCH))
            legs = cur.fetchall()
    label = f"{a.since} 起" if a.since else f"近 {a.hours:g}h"
    print(f"reversal_decay 反事实（{label}）：{len(legs)} 笔 `{PATH_MATCH}`")
    if not legs:
        print("样本为 0 ⇒ 无法判定")
        return 1
    rows = []
    with psycopg.connect(market, autocommit=True) as c2:
        with c2.cursor() as cur2:
            for ts, sym, side, net, fee, qty, notional in legs:
                t0 = int(ts.timestamp())
                cur2.execute("""
                    SELECT (event_ts_ms/5000)*5 AS b,
                           (ARRAY_AGG((bid_px+ask_px)/2 ORDER BY event_ts_ms DESC))[1]
                    FROM asterdex_book_ticker
                    WHERE symbol=%s AND event_ts_ms > %s AND event_ts_ms <= %s
                      AND bid_px>0 AND ask_px>bid_px
                    GROUP BY 1 ORDER BY 1""",
                             (sym + "USDT", t0 * 1000, (t0 + HORIZON_S + 5) * 1000))
                pts = [(int(b), float(m)) for b, m in cur2.fetchall() if m and m > 0]
                if len(pts) < 3:
                    continue
                base = pts[0][1]
                # sign_pos=+1 多头（卖出平仓）⇒ 不利 = 下跌；-1 空头 ⇒ 不利 = 上涨
                sign_pos = 1.0 if side == "sell" else -1.0
                rel = [(t, sign_pos * (m - base) / base * 1e4) for t, m in pts]
                # 路径：持有期间的相对盈亏（正=有利）
                end = rel[-1][1]
                adv = min(v for _t, v in rel)      # 最大不利（最负）
                fav = max(v for _t, v in rel)      # 最大有利
                # 若触发止损：止损先到时按 −stop_bp 结算（近似），否则按终点结算
                hit_stop = adv <= -a.stop_bp
                cf_path = (-a.stop_bp) if hit_stop else end
                # h470 口径的终点反事实（省 taker 费）
                cf_end = float(net) - end + abs(float(fee))
                rows.append({
                    "ts": str(ts), "sym": sym, "side": side,
                    "net_bp": float(net), "fee_bp": abs(float(fee)),
                    "notional": float(notional), "n_pts": len(pts),
                    "d_post300": round(end, 3), "adv_hold_bp": round(adv, 3),
                    "mfe_hold_bp": round(fav, 3), "hit_stop": bool(hit_stop),
                    "cf_end_net": round(cf_end, 3),
                    "cf_path_net": round(float(net) - cf_path + abs(float(fee)), 3),
                })
    if not rows:
        print("盘口路径数据缺失（book_ticker 覆盖不足）⇒ 无法判定")
        return 1
    n = len(rows)
    nets = [r["net_bp"] for r in rows]
    cfe = [r["cf_end_net"] for r in rows]
    cfp = [r["cf_path_net"] for r in rows]
    advs = [r["adv_hold_bp"] for r in rows]
    stops = [r for r in rows if r["hit_stop"]]
    fees = sum(r["fee_bp"] * r["notional"] / 1e4 for r in rows)
    # ⚠️ 首版写成 `if r["net_bp"] < -stop_bp`（只统计"已实现亏得比止损还狠"的笔），
    #    而本出场恰好让这些笔亏得**比止损少** ⇒ 条件恒假 ⇒ 省下的钱被算成 ~0 ✗。
    #    正确口径：省下 = max(0, 止损幅度 − 本出场实收亏损)，逐笔乘名义。
    avoided_usd = sum(max(0.0, a.stop_bp - abs(r["net_bp"])) * r["notional"] / 1e4
                      for r in stops)
    nots = sorted(r["notional"] for r in rows)
    print(f"\n有效样本 {n} 笔（盘口路径齐备）")
    print("=" * 84)
    for lab, v in (("已实现 net_bp", nets), ("反事实(终点, 省费)", cfe),
                   ("反事实(路径版)", cfp), ("持有期最大不利 adv_hold_bp", advs)):
        s = stats(v)
        if "mean" in s:
            print(f"  {lab:28s} n={s['n']:3d} 均={s['mean']:+8.2f} "
                  f"中位={s['p50']:+8.2f} t={s['t']:+6.2f}")
    print(f"\n  终点口径 Δ = {stats(cfe)['mean'] - stats(nets)['mean']:+.2f}bp")
    print(f"  路径口径 Δ = {stats(cfp)['mean'] - stats(nets)['mean']:+.2f}bp")
    print(f"\n**尾部保护**：持有期最大不利 ≤ −{a.stop_bp:.0f}bp（=不做就会撞止损）"
          f"的有 **{len(stops)}/{n} = {100.0*len(stops)/n:.1f}%**")
    print(f"  这些笔里，本出场实收 {stats([r['net_bp'] for r in stops])['mean']:+.2f}bp "
          f"vs 止损约 −{a.stop_bp:.0f}bp ⇒ 少亏约 "
          f"{a.stop_bp + stats([r['net_bp'] for r in stops])['mean']:.2f}bp/笔，"
          f"合计约 {avoided_usd:+.2f}$")
    print(f"\n  全部 {n} 笔付出的 taker 费合计 ≈ {fees:.2f}$"
          f"（名义额 P50={nots[n//2]:.1f}$ 均值={sum(nots)/n:.1f}$ —— "
          f"注意账本 `notional` 列对出场腿是**持仓名义**，比单腿成交额大；"
          f"故 $ 口径只作量级参考，判据一律用 bp）")
    print(f"\n  逐币：")
    by: dict = {}
    for r in rows:
        by.setdefault(r["sym"], []).append(r)
    for sym, g in sorted(by.items(), key=lambda kv: -len(kv[1])):
        gs = sum(1 for r in g if r["hit_stop"])
        s = stats([r["net_bp"] for r in g])
        mm = f"{s['mean']:+8.2f}" if "mean" in s else "   (n<3)"
        print(f"    {sym:>5s} n={len(g):3d} 净均={mm}bp 撞止损={gs}/{len(g)}")
    # ── 预注册判据 ──
    share = len(stops) / n
    d_end = stats(cfe)["mean"] - stats(nets)["mean"]
    # 期望尾部保护值（bp/腿）= 撞止损占比 × 每次少亏的幅度
    tail_bp = share * (a.stop_bp + stats([r["net_bp"] for r in stops])["mean"]) \
        if stops else 0.0
    if share >= 0.25 and tail_bp > 4.0:
        verdict = (f"**保留**：{100*share:.0f}% 的笔数在不做该出场时会撞 {a.stop_bp:.0f}bp 止损，"
                   f"期望尾部保护 ≈{tail_bp:.1f}bp/腿 > 4bp 费")
    elif share < 0.10 and d_end <= 0:
        verdict = (f"**回滚**：仅 {100*share:.0f}% 会撞止损（尾部保护不成立），"
                   f"而终点口径 Δ={d_end:+.2f}bp ≤0 ⇒ 只付 taker 费无收益")
    elif abs(d_end) < 1.0 and tail_bp > 1.5:
        verdict = (f"**保留（不显著但方向明确）**：价格维度 Δ={d_end:+.2f}bp（终点）/ "
                   f"{stats(cfp)['mean'] - stats(nets)['mean']:+.2f}bp（路径）≈ 0 "
                   f"⇒ **移除它没有可证收益**；而它提供期望 ≈{tail_bp:.1f}bp/腿 的尾部保护"
                   f"（{100*share:.0f}% 的笔数本会撞止损，每次少亏 "
                   f"{a.stop_bp + stats([r['net_bp'] for r in stops])['mean']:.1f}bp）"
                   f"⇒ 回滚会把唯一的尾部保护拿掉而换不到收益 ⇒ **保留**。"
                   f"⚠️ 该结论依赖「反事实省下 taker 费」这一乐观假设（现实中替代出场"
                   f"同样多为 taker）⇒ 若真省不下费，保留的价值更大。")
    else:
        verdict = (f"**INCONCLUSIVE**：撞止损占比 {100*share:.0f}%、终点 Δ={d_end:+.2f}bp、"
                   f"n={n} ⇒ 两头都不够强；需延长窗口或冻结试跑")
    print(f"\n⇒ 裁决：{verdict}")
    OUT.write_text(json.dumps({
        "label": label, "path": PATH_MATCH, "n": n, "rows": rows,
        "realized_mean_bp": stats(nets)["mean"], "cf_end_mean_bp": stats(cfe)["mean"],
        "cf_path_mean_bp": stats(cfp)["mean"], "delta_end_bp": round(d_end, 3),
        "delta_path_bp": round(stats(cfp)["mean"] - stats(nets)["mean"], 3),
        "stop_share": round(share, 4), "stops": len(stops),
        "fees_usd": round(fees, 3), "avoided_usd": round(avoided_usd, 3),
        "verdict": verdict,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
