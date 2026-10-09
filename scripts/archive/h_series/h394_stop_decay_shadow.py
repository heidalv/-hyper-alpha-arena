# -*- coding: utf-8 -*-
"""H394 #16③ 影子测验：止损后降该币腿量一档（post-stop per-symbol decay）。

背景：h393 否定 #16 的"入场方向过滤"后，h375 把优先级转到方案③——
止损触发后 30 分钟内该币腿量减半（compound 按币衰减），不损腿速、纯事后风控。
本脚本量化该规则在 #2 时代窗口的期望影响：

  对每个 stop_loss_taker 行（符号 S，时刻 T）：
    受影响腿 = S 在 (T, T+30min] 的全部 lane_ledger 行（含 maker 腿）；
    规则效果 = 这些腿的 notional × (1 − decay_frac)，即净 USD 按同比例缩放：
      ΔUSD = Σ (net_bp/1e4 × notional) × (decay_frac − 1)
  若受影响腿整体为负（连环止损 + 逆选择）⇒ ΔUSD > 0（少亏），规则划算。

口径：net_usd = net_bp/1e4 × notional（逐腿，Σbp×Σnotional 是错的）。
重叠窗口不叠加（30min 内多笔止损仍只减一档）。
用法: python scripts/h394_stop_decay_shadow.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h394_stop_decay_shadow.json"

WINDOW_START = "2026-09-27 13:00:00+08"
DECAY_WINDOW_SEC = 1800.0
DECAY_FRACS = [0.25, 0.5]


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
    import psycopg
    with psycopg.connect(read_env_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, ts, net_bp, notional, meta_json->>'exit_path' AS ep,
                       meta_json->>'side' AS side
                FROM lane_ledger
                WHERE lane_id='mm_asterdex' AND ts > %s::timestamptz
                ORDER BY ts
            """, (WINDOW_START,))
            rows = [(r[0], r[1], float(r[2] or 0.0), float(r[3] or 0.0),
                     r[4] or "", r[5] or "") for r in cur.fetchall()]

    # 止损时刻按币排序
    stops_by_sym = {}
    for sym, ts, bp, notional, ep, side in rows:
        if ep == "stop_loss_taker":
            stops_by_sym.setdefault(sym, []).append(ts)

    total_stops = sum(len(v) for v in stops_by_sym.values())
    print(f"窗口内行数 {len(rows)}；止损腿 {total_stops} 笔，"
          f"涉及币 {sorted(stops_by_sym)}", flush=True)

    # 聚类诊断：每笔止损后 30min 内同币是否还有止损
    cluster = 0
    for sym, tss in stops_by_sym.items():
        for i, t in enumerate(tss):
            for t2 in tss[i + 1:]:
                if 0 < (t2 - t).total_seconds() <= DECAY_WINDOW_SEC:
                    cluster += 1
                elif (t2 - t).total_seconds() > DECAY_WINDOW_SEC:
                    break
    print(f"连环止损（30min 内同币再止损）对数：{cluster}", flush=True)

    per_sym = {}
    for frac in DECAY_FRACS:
        tot_affected = 0
        tot_usd = 0.0
        per_sym[frac] = {}
        # 每币构造 [stop_ts, stop_ts+30min] 的受影响区间
        for sym, tss in stops_by_sym.items():
            tss_sorted = sorted(tss)
            n_aff = 0
            usd_aff = 0.0
            for sym2, ts, bp, notional, ep, side in rows:
                if sym2 != sym:
                    continue
                if any(0 < (ts - st).total_seconds() <= DECAY_WINDOW_SEC
                       for st in tss_sorted):
                    n_aff += 1
                    usd_aff += bp / 1e4 * notional
            tot_affected += n_aff
            tot_usd += usd_aff
            per_sym[frac][sym] = {"affected_legs": n_aff, "usd": round(usd_aff, 3)}
        # 规则净效果 = −frac × Σ(受影响腿 USD)
        print(f"\ndecay_frac={frac}: 受影响 {tot_affected} 腿，"
              f"其净 USD = {tot_usd:+.3f}")
        print(f"  规则净效果 ΔUSD = {(-frac * tot_usd):+.3f} "
              f"({'划算 ✓' if tot_usd < 0 else '不划算 ✗'}（受影响腿整体为"
              f"{'负' if tot_usd < 0 else '正'}）)")
        print(f"  分币种 USD：{json.dumps(per_sym[frac], ensure_ascii=False)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "n_rows": len(rows), "n_stops": total_stops,
        "cluster_pairs": cluster,
        "per_frac": per_sym,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
