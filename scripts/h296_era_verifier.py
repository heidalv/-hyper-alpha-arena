# -*- coding: utf-8 -*-
"""H296 模型时代验证器：新栈上线后的连续成绩单 + 同口径昨日对照。

# 用途：目标最终判据「稳定正收益」的客观度量。
# 口径：
    A. 自 era_start（默认 2026-09-23 11:19，模型模式上线时刻）逐小时：腿数/净额/出口分布
    B. 累计：每腿加权净额 bp、净美元、maker 腿与出口腿分开
    C. 对照：**同星期同时段的前一天**（昨 11:19 起同长度窗口）旧策略成绩
# 判据建议（写入报告）：连续 ≥6 小时 单腿净额 ≥ 0 且日亏闸未触发 ⇒ 达标。

# 用法

    python scripts/h296_era_verifier.py
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h296_era_verifier.json"
LANE = "mm_asterdex"
ERA_START = "2026-09-23 11:19+08"


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None, help="默认读 lane_registry.meta.stats_since（账户重置=新时代）")
    a = ap.parse_args()

    import psycopg

    era_start = a.since
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            if not era_start:
                cur.execute("SELECT meta_json->>'stats_since' FROM lane_registry WHERE lane_id=%s",
                            (LANE,))
                row = cur.fetchone()
                era_start = row[0] if row and row[0] else ERA_START
            cur.execute("""
                SELECT date_trunc('hour', ts) AS h, count(*),
                       round((sum(net_bp*notional/1e4))::numeric,2),
                       count(*) FILTER (WHERE meta_json->>'exit_path'='reversal_decay_taker'),
                       count(*) FILTER (WHERE meta_json->>'exit_path'='stop_loss_taker'),
                       count(*) FILTER (WHERE meta_json->>'exit_path'='take_profit_taker')
                FROM lane_ledger WHERE lane_id=%s AND ts >= %s
                GROUP BY 1 ORDER BY 1
            """, (LANE, era_start))
            hourly = [{"h": str(r[0]), "n": int(r[1]), "net_usd": float(r[2]),
                       "decay": int(r[3]), "sl": int(r[4]), "tp": int(r[5])}
                      for r in cur.fetchall()]
            cur.execute("""
                SELECT count(*), round((sum(net_bp*notional/1e4))::numeric,2),
                       round((sum(net_bp*notional)/NULLIF(sum(notional),0))::numeric,3),
                       round((sum(fee_bp*notional/1e4))::numeric,2)
                FROM lane_ledger WHERE lane_id=%s AND ts >= %s
            """, (LANE, era_start))
            n, usd, wbp, fee = cur.fetchone()
            # 对照：前一天同时段（同长度窗口）
            cur.execute("""
                SELECT count(*), round((sum(net_bp*notional/1e4))::numeric,2),
                       round((sum(net_bp*notional)/NULLIF(sum(notional),0))::numeric,3)
                FROM lane_ledger WHERE lane_id=%s
                  AND ts >= (%s::timestamptz - interval '24 hours')
                  AND ts <  (%s::timestamptz - interval '24 hours')
                    + (now() - %s::timestamptz)
            """, (LANE, era_start, era_start, era_start))
            n0, usd0, wbp0 = cur.fetchone()

    hours = len(hourly)
    report = {
        "as_of": str(psycopg.connect(dsn()) and __import__("datetime").datetime.now().astimezone().isoformat()),
        "era_start": era_start, "hours_elapsed": hours,
        "era": {"n": n, "net_usd": float(usd), "wnet_bp": float(wbp), "fee_usd": float(fee)},
        "baseline_yesterday": {"n": n0 or 0, "net_usd": float(usd0 or 0), "wnet_bp": float(wbp0 or 0)},
        "hourly": hourly,
        "positive_hours": sum(1 for h in hourly if h["net_usd"] > 0),
        "verdict_hint": ("达标候选" if (hours >= 6 and float(wbp or 0) >= 0
                          and sum(1 for h in hourly if h["net_usd"] > 0) >= hours * 0.5)
                         else "继续积累" if hours < 6 else "未达标"),
    }
    report["as_of"] = __import__("datetime").datetime.now().astimezone().isoformat()

    print("=" * 96)
    print("H296  模型时代验证")
    print("=" * 96)
    print(f"  时代起点 {era_start}  已 {hours} 小时")
    print(f"  本时代: {n} 腿  净 {float(usd):+.2f}$  每腿 {float(wbp):+.3f}bp  费 {float(fee):+.2f}$")
    print(f"  昨对照: {n0 or 0} 腿  净 {float(usd0 or 0):+.2f}$  每腿 {float(wbp0 or 0):+.3f}bp")
    for h in hourly:
        print(f"    {h['h'][11:16]}  n={h['n']:>4} 净={h['net_usd']:+8.2f}$  "
              f"decay={h['decay']} sl={h['sl']} tp={h['tp']}")
    print(f"  正小时 {report['positive_hours']}/{hours}  判定: {report['verdict_hint']}")
    # 原子写：先写临时文件再替换，避免并发读者读到半截 JSON（2026-09-25 实测竞态）
    _tmp = OUT.with_suffix(".json.tmp")
    _tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _tmp.replace(OUT)
    print(f"  已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
