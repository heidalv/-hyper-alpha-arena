# -*- coding: utf-8 -*-
"""H198 当前时代基线 —— 长跑起点，锁定"从哪一刻开始算"。

# 为什么要单独一个脚本

用户决定"冻结参数，跑一个月"。一个月后要回答"这一个月怎么样"，
前提是**起点唯一且可复查**。

本次踩到的坑：`lane_ledger` 里 9 月 9 日以来的累计净额是 **−$309.66**，
而心跳权益是 **$299.25** —— 看起来自相矛盾（亏了 309 却还有 299 本金）。
原因：`lane_registry.meta.stats_since = 2026-09-21T18:00:34`
⇒ **车道统计周期在 18:00 重置过**，账本里的历史是**旧时代**的遗留。

⇒ 任何"跑了多久、赚亏多少"的结论**必须锚定 stats_since**，
   否则会把重置前的旧账算进新实验。

# 用法

    python scripts/h198_epoch_baseline.py            # 当前时代
    python scripts/h198_epoch_baseline.py --json     # 打给别的脚本用
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
START_EQUITY = 300.0


def dsn() -> str:
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


def stats_since() -> datetime | None:
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->>'stats_since' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            r = cur.fetchone()
    if not r or not r[0]:
        return None
    try:
        return datetime.fromisoformat(r[0])
    except Exception:
        return None


def heartbeat() -> dict:
    try:
        return json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    since = stats_since()
    if since is None:
        print("  ✗ 读不到 stats_since")
        return 1

    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT count(*) FILTER (WHERE meta_json->>'flatten' NOT IN ('true','True')),
                       count(*) FILTER (WHERE meta_json->>'flatten' IN ('true','True')),
                       coalesce(sum(net_bp*notional/1e4) FILTER
                         (WHERE meta_json->>'flatten' NOT IN ('true','True')),0),
                       coalesce(sum(net_bp*notional/1e4) FILTER
                         (WHERE meta_json->>'flatten' IN ('true','True')),0),
                       coalesce(sum(net_bp*notional/1e4),0),
                       coalesce(sum(notional),0),
                       min(ts), max(ts),
                       count(*) FILTER (WHERE meta_json->>'flatten' IN ('true','True')
                         AND (meta_json->>'fill_px') IS NOT NULL
                         AND (meta_json->>'mid_px') IS NOT NULL
                         AND (CASE WHEN lower(meta_json->>'side')='buy' THEN 1 ELSE -1 END)
                             * ((meta_json->>'fill_px')::float - (meta_json->>'mid_px')::float)
                             / NULLIF((meta_json->>'mid_px')::float,0) * 1e4 > 0)
                FROM lane_ledger WHERE lane_id=%s AND ts >= %s
            """, (LANE, since))
            mk, fl, mku, flu, net, notl, t0, t1, paid = cur.fetchone()

    hb = heartbeat()
    equity = float(hb.get("equity") or 0.0)
    hrs = ((t1 - t0).total_seconds() / 3600.0) if (t0 and t1) else 0.0
    mk, fl = int(mk or 0), int(fl or 0)
    mku, flu = float(mku or 0), float(flu or 0)
    net, notl = float(net or 0), float(notl or 0)

    out = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "stats_since": since.isoformat(),
        "epoch_hours": round(hrs, 3),
        "equity": equity,
        "start_equity": START_EQUITY,
        "equity_delta": round(equity - START_EQUITY, 4),
        "ledger_net_usd": round(net, 4),
        "ledger_vs_equity_gap": round(net - (equity - START_EQUITY), 4),
        "maker_n": mk, "flat_n": fl,
        "maker_usd": round(mku, 4), "flat_usd": round(flu, 4),
        "notional": round(notl, 2),
        "net_bp": round(net / notl * 1e4, 4) if notl > 0 else None,
        "net_usd_per_hour": round(net / hrs, 4) if hrs > 0 else None,
        "equity_per_hour": round((equity - START_EQUITY) / hrs, 4) if hrs > 0 else None,
        "flat_share_pct": round(100.0 * fl / max(mk + fl, 1), 2),
        "paid_cross_pct": round(100.0 * int(paid or 0) / fl, 1) if fl else None,
        "symbols": hb.get("symbols"),
    }

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print("=" * 92)
    print("H198  当前时代基线（长跑起点）")
    print("=" * 92)
    print(f"  stats_since   {since:%Y-%m-%d %H:%M:%S %Z}   （车道统计重置点）")
    print(f"  已运行        {hrs:.2f} 小时")
    print(f"  宇宙          {out['symbols']}")
    print()
    print(f"  起始本金      ${START_EQUITY:.2f}")
    print(f"  当前权益      ${equity:.2f}   ⇒ Δ {out['equity_delta']:+.4f}")
    print(f"  账本净额      ${net:+.4f}")
    print(f"  两者差        ${out['ledger_vs_equity_gap']:+.4f}"
          f"   （不为 0 说明账本口径与账户口径有差异，需注明）")
    print()
    print(f"  maker 腿 {mk:>6}   ${mku:>+10.4f}")
    print(f"  flatten 腿 {fl:>4}   ${flu:>+10.4f}   （占比 {out['flat_share_pct']}%，"
          f"穿价率 {out['paid_cross_pct']}%）")
    print(f"  名义          ${notl:>12,.0f}")
    print(f"  净额 bp       {out['net_bp']:+.4f} bp")
    print()
    print(f"  **按小时**    账本 {out['net_usd_per_hour']:+.4f} $/h   "
          f"权益 {out['equity_per_hour']:+.4f} $/h")
    if out["net_usd_per_hour"]:
        print(f"  **外推一个月** 账本 {out['net_usd_per_hour']*24*30:+.1f} USD   "
              f"权益 {out['equity_per_hour']*24*30:+.1f} USD")
    print()
    print("  ⚠️ 外推仅供参考：4 小时样本无法代表 30 天，")
    print("     且已实测 corr(波动, 净额) = −0.445 ⇒ 结果高度依赖这一个月是什么行情。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
