"""h525：当前时代**逐币费用/逆向选择分解**——BNB 的稳态渗漏到底是什么？

h524：当前时代 BNB 486 腿、−0.851bp/腿（t=−2.67，唯一显著），但只有 2 条止损腿
⇒ 亏损不是止损造成的，是「每腿稳态渗漏」。渗漏只有两个可能来源：
  (1) **手续费**（入场/出场走了 taker，或 maker 费率并非 0）；
  (2) **逆向选择**（成交后中价继续朝不利方向走）。
本脚本把 lane_ledger 里所有与费用/净额相关的列都拆出来逐币对比，并给出裁决：
若费用≈0 ⇒ 只能靠几何/状态（改价差、改挂单位置）；若费用占大头 ⇒ 应查费率与 taker 腿占比。

用法：python scripts/h525_fee_decomp.py [--hours 0]  # 0 = 用当前时代起点
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h525_fee_decomp.json"
ERA_DEFAULT = "2026-09-28 13:00"


def read_env_dsn() -> str:
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
    return url


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=0.0)
    ap.add_argument("--since", default=ERA_DEFAULT)
    a = ap.parse_args()
    dsn = read_env_dsn()
    now = dt.datetime.now()
    if a.hours > 0:
        since, hours = now - dt.timedelta(hours=a.hours), a.hours
        label = f"近 {a.hours:g}h"
    else:
        since = dt.datetime.strptime(a.since, "%Y-%m-%d %H:%M")
        hours = (now - since).total_seconds() / 3600.0
        label = f"{a.since} 起（当前时代）"
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name='lane_ledger' ORDER BY ordinal_position")
            cols = cur.fetchall()
            print("lane_ledger 列：")
            for i in range(0, len(cols), 4):
                print("   " + " | ".join(f"{n}:{t[:14]}" for n, t in cols[i:i + 4]))
            names = {n for n, _ in cols}
            # 找出所有疑似费用/净额列
            fee_cols = [n for n in names if "fee" in n.lower()]
            print(f"\n疑似费用列：{fee_cols or '（无）'}")
            # 逐币：名义额、净额 + **真实 bp 分解列**
            cur.execute("""
                SELECT symbol, count(*) AS legs,
                       sum(notional)::float8 AS notional,
                       sum(net_bp*notional/1e4)::float8 AS net_usd,
                       avg(net_bp)::float8 AS net_bp,
                       (sum(price_bp*notional)/NULLIF(sum(notional),0))::float8 AS price_bp,
                       (sum(fee_bp*notional)/NULLIF(sum(notional),0))::float8 AS fee_bp,
                       (sum(slippage_bp*notional)/NULLIF(sum(notional),0))::float8 AS slip_bp,
                       (sum(funding_bp*notional)/NULLIF(sum(notional),0))::float8 AS fund_bp,
                       (sum(spread_bp*notional)/NULLIF(sum(notional),0))::float8 AS spread_bp,
                       COALESCE(sum(points_usd),0)::float8 AS points_usd
                FROM lane_ledger WHERE lane_id=%s AND ts >= %s GROUP BY symbol
                ORDER BY legs DESC""", (LANE, since))
            base = {r[0]: r for r in cur.fetchall()}
            # meta_json 里所有出现过的 fee 相关键
            cur.execute("""
                SELECT k, count(*) FROM (
                  SELECT jsonb_object_keys(meta_json) AS k FROM lane_ledger
                  WHERE lane_id=%s AND ts >= %s) t
                GROUP BY k ORDER BY count(*) DESC""", (LANE, since))
            keys = cur.fetchall()
            print("\nmeta_json 键频次（当前时代）：")
            print("   " + "、".join(f"{k}×{n}" for k, n in keys[:24]))
            fkeys = [k for k, _ in keys if "fee" in k.lower() or "rebate" in k.lower()
                     or "taker" in k.lower() or "maker" in k.lower()]
            print(f"\n费用/角色相关键：{fkeys or '（无）'}")
    print(f"\n逐币 bp 分解 · {label}（{hours:.2f}h）· 各列按名义额加权")
    print("=" * 118)
    print(f"{'币':>6s} {'腿数':>6s} {'净额$':>9s} {'净bp':>8s} {'价差bp':>8s} "
          f"{'费用bp':>8s} {'滑点bp':>8s} {'资金bp':>8s} {'价差+费用+滑点':>13s} "
          f"{'积分$':>8s}")
    out = []
    for s, (sym, n, notional, net_usd, net_bp, pbp, fbp, slbp, fubp, spbp, pts) in base.items():
        resid = (pbp or 0) + (fbp or 0) + (slbp or 0)
        print(f"{s:>6s} {n:6d} {net_usd:9.3f} {net_bp:+8.3f} {pbp:+8.3f} "
              f"{fbp:+8.3f} {slbp:+8.3f} {fubp:+8.3f} {resid:+13.3f} {pts:8.2f}")
        out.append({"sym": s, "legs": n, "notional": round(notional, 1),
                    "net_usd": round(net_usd, 3), "net_bp": round(net_bp, 3),
                    "price_bp": round(pbp, 3), "fee_bp": round(fbp, 3),
                    "slippage_bp": round(slbp, 3), "funding_bp": round(fubp, 3),
                    "spread_bp": round(spbp, 3), "points_usd": round(pts, 3),
                    "resid_bp": round(resid, 3)})
    # ── 费用取值分布：是单峰（统一费率）还是双峰（maker/taker）？决定能否规避 ──
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, round(fee_bp::numeric, 4) AS f, count(*) AS n,
                       sum(notional)::float8
                FROM lane_ledger WHERE lane_id=%s AND ts >= %s
                GROUP BY 1, 2 HAVING count(*) >= 2 ORDER BY 1, 3 DESC""", (LANE, since))
            frows = cur.fetchall()
            # 正确口径：名义加权净 bp
            cur.execute("""
                SELECT symbol,
                       (sum(net_bp*notional)/NULLIF(sum(notional),0))::float8 AS w_net,
                       (sum(price_bp*notional)/NULLIF(sum(notional),0))::float8 AS w_px,
                       (sum(fee_bp*notional)/NULLIF(sum(notional),0))::float8 AS w_fee,
                       (sum(slippage_bp*notional)/NULLIF(sum(notional),0))::float8 AS w_slip
                FROM lane_ledger WHERE lane_id=%s AND ts >= %s GROUP BY symbol
                ORDER BY 2""", (LANE, since))
            wrows = cur.fetchall()
    if frows:
        print("\nfee_bp 取值分布（≥2 次的取值；双峰 ⇒ 存在 maker/taker 两种费率）")
        for s in sorted({r[0] for r in frows}):
            vals = [r for r in frows if r[0] == s]
            txt = "、".join(f"{v:+.4f}bp×{n}" for _, v, n, _ in vals[:6])
            print(f"  {s:>6s}: {txt}")
    print("\n**正确口径**（名义加权；net_bp 的简单均值会与分解列不可比）")
    print(f"{'币':>6s} {'加权净bp':>10s} {'价差bp':>9s} {'费用bp':>9s} {'滑点bp':>9s} "
          f"{'费用占净':>9s}")
    for s, wn, wp, wf, ws in wrows:
        print(f"{s:>6s} {wn:+10.3f} {wp:+9.3f} {wf:+9.3f} {ws:+9.3f} "
              f"{(100.0*wf/wn if wn else float('nan')):8.0f}%")
    tot_n = sum(r["notional"] for r in out)
    w_fee = sum(r["fee_bp"] * r["notional"] for r in out) / tot_n if tot_n else 0.0
    w_net = sum(r["net_bp"] * r["notional"] for r in out) / tot_n if tot_n else 0.0
    print(f"\n全币加权：净 {w_net:+.3f}bp/腿，费用 {w_fee:+.3f}bp/腿 "
          f"⇒ 费用占净额 {100.0*w_fee/w_net if w_net else float('nan'):.0f}%")
    bnb = next((r for r in out if r["sym"] == "BNB"), None)
    if bnb:
        print(f"BNB：净 {bnb['net_bp']:+.3f}bp，其中费用 {bnb['fee_bp']:+.3f}、"
              f"价差 {bnb['price_bp']:+.3f}、滑点 {bnb['slippage_bp']:+.3f} "
              f"⇒ 主因 = "
              + ("**手续费**" if abs(bnb["fee_bp"]) > abs(bnb["price_bp"]) else "**价差/逆向选择**"))
    # ── taker 腿（fee_bp = −4）的 exit_path 分布：决定该改哪个机制 ──
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(NULLIF(meta_json->>'exit_path',''),'(入场腿)') AS p,
                       count(*) AS n,
                       sum(net_bp*notional/1e4)::float8 AS net_usd,
                       (sum(net_bp*notional)/NULLIF(sum(notional),0))::float8 AS w_net,
                       sum(notional)::float8 AS notional
                FROM lane_ledger
                WHERE lane_id=%s AND ts >= %s AND fee_bp < -1
                GROUP BY 1 ORDER BY n DESC""", (LANE, since))
            tk = cur.fetchall()
            cur.execute("""
                SELECT COALESCE(NULLIF(meta_json->>'exit_path',''),'(入场腿)') AS p,
                       count(*) AS n,
                       sum(net_bp*notional/1e4)::float8 AS net_usd,
                       sum(notional)::float8 AS notional,
                       count(*) FILTER (WHERE fee_bp < -1) AS tk_n
                FROM lane_ledger WHERE lane_id=%s AND ts >= %s
                GROUP BY 1 ORDER BY 3 ASC NULLS LAST""", (LANE, since))
            allp = cur.fetchall()
    tot_tk = sum(r[1] for r in tk)
    tot_tk_usd = sum(r[2] or 0 for r in tk)
    fee_tk = sum(-4.0e-4 * r[4] for r in tk)
    print(f"\n**taker 腿（fee_bp<−1）的 exit_path 分布**：{tot_tk} 腿，"
          f"净 {tot_tk_usd:+.2f}$，其中**费用 {fee_tk:+.2f}$**"
          f"（占这些腿亏损的 {(100.0*fee_tk/tot_tk_usd if tot_tk_usd else float('nan')):.0f}%）")
    print(f"{'exit_path':<26s} {'腿数':>6s} {'净$':>9s} {'加权净bp':>10s} "
          f"{'名义$':>11s} {'费用$':>9s} {'非费用净$':>11s}")
    for p, n, usd, wn, nt in tk:
        fee = -4.0e-4 * nt
        print(f"{(p or '')[:26]:<26s} {n:6d} {usd:+9.3f} {wn:+10.3f} "
              f"{nt:11.1f} {fee:+9.3f} {usd - fee:+11.3f}")
    print("\n**全部 exit_path 明细**（按净额升序）")
    for p, n, usd, nt, tkn in allp:
        print(f"  {(p or '')[:30]:<30s} {n:6d} 净 {usd:+9.3f}$ "
              f"（{usd/hours:+.3f}$/h） 名义 {nt:10.1f}$ taker {tkn:4d} 腿")
    print("\n⇒ 判读：某路径「净额 − 费用」若为正，则该路径改成 maker 即可翻正。")
    OUT.write_text(json.dumps({"label": label, "hours": round(hours, 3),
                               "ledger_columns": [c[0] for c in cols],
                               "fee_cols": fee_cols,
                               "meta_keys": [k for k, _ in keys],
                               "fee_like_keys": fkeys, "coins": out},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
