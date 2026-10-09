"""h535：**停摆事故的完整因果链取证**（只读，一条命令出结论）。

已确认的链条（本会话逐一取证）：
  1. 车道自 04:32:38 起零腿，worker 仍正常 tick 且在报价
     ⇒ 排除"worker 挂了"与"波动闸"（avg_sigma_all=0.16）；
  2. `fills` 计数自 04:32 起不再增长 ⇒ **成交判定拿不到数据**；
  3. market 库 `market_trades_aggregated` / `market_orderbook_snapshots` /
     `asterdex_trades` **三张表同时停在 04:32:2x–4x** ⇒ 采集侧断，不是引擎侧；
  4. `asterdex_stream_health` 新鲜但 `msgs_total==reconnects==188`、`last_event_ms=0`
     ⇒ 采集器活着但**每次连接都被重置**；
  5. 采集器确实走代理（`L1_PROXY` 默认 `http://127.0.0.1:1080`）；
     经 1080 的 TLS **握手收不到数据**、经链式口 18080 返回 **502**、
     直连被 reset；SS 上游 `8.211.172.14:55620` TCP 可达但**不转发**
     ⇒ **根因 = Shadowsocks 上游节点故障（外部，需换节点/续期）**。

本脚本补最后一环：**各币在停摆前是否真有数据**，以及当前 ingester 的币种表
是否覆盖车道在役币——这决定"代理恢复后车道能否自己活过来"。

用法：python scripts/h535_incident_forensics.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE_SYMS = ["BNB", "NEAR", "ARB", "XRP", "ENA"]


def read_env() -> dict:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def main() -> int:
    env = read_env()
    dsn = env.get("MARKET_DATABASE_URL") or env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        dsn = dsn.replace(j, "")
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT now()")
            now = cur.fetchone()[0]
            print(f"market 库 now() = {now:%Y-%m-%d %H:%M:%S}")
            # 停摆前 12h 各币的数据覆盖（timestamp = epoch_ms）
            cut = int((now.timestamp() - 12 * 3600) * 1000)
            cur.execute("""
                SELECT symbol, count(*) AS n,
                       to_timestamp(min(timestamp)/1000.0) AS first_t,
                       to_timestamp(max(timestamp)/1000.0) AS last_t
                FROM market_trades_aggregated
                WHERE timestamp > %s
                GROUP BY symbol ORDER BY n DESC""", (cut,))
            rows = cur.fetchall()
            print(f"\n停摆前 12h 内 market_trades_aggregated 各币覆盖"
                  f"（桶数 / 首 / 末）")
            print("=" * 92)
            have = {}
            for s, n, f, l in rows:
                bare = s.replace("USDT", "").replace("-", "").upper()
                mark = "★" if bare in LANE_SYMS else " "
                have[bare] = (n, l)
                print(f" {mark}{s:>10s} {n:7d}  {f:%m-%d %H:%M}  →  {l:%m-%d %H:%M}")
            print("\n车道在役币覆盖检查（★ = 在役）")
            missing = [s for s in LANE_SYMS if s not in have]
            for s in LANE_SYMS:
                if s in have:
                    n, l = have[s]
                    print(f"  {s:>5s} ✓ {n} 桶，最后 {l:%H:%M:%S}")
                else:
                    print(f"  {s:>5s} ✗ **停摆前 12h 就没有数据**")
            if missing:
                print(f"\n⇒ **{missing} 在停摆前就没有行情** ⇒ 即使代理恢复，"
                      f"这些币也不会自动有数据 ⇒ **必须把 ingester 的 --symbols "
                      f"改成覆盖车道在役币**（当前运行的实例只有 "
                      f"BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT）。")
            else:
                print("\n⇒ 车道 5 币停摆前都有数据 ⇒ 代理恢复后采集器重启即可自愈；"
                      "但**仍需确认重启时的 --symbols 覆盖这 5 个币**。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
