"""h550：**"已有连接活着 vs 新建隧道失败"的精确判定**。

矛盾现场（2026-09-29 09:3xL）：
  · `h534/h549` 显示 `asterdex_trades` / `market_trades_aggregated` **在写**（连续按分钟有行）；
  · 但 `h536` 的代理探针（经 127.0.0.1:1080 新建 CONNECT + TLS 到
    `fapi/fstream.asterdex.com`）**每次都 TLS 失败**。
两种解释的后果完全不同：
  (A) 节点"只允许既有长连接、拒绝新建" ⇒ **采集器一重启就会连不上**（04:32 事故的同型）
      ⇒ 必须立刻换节点；
  (B) 我的探针本身有问题（例如 SS 客户端对并发/短连接限流，或探针目标被节点单独拦）
      ⇒ 采集其实健康，别乱动。

本脚本用**精确口径**（全部换成 epoch 秒做差，不再依赖时区推断）回答：
  1. 各链路表"距今多少秒"、最近 60 秒有多少行；
  2. `asterdex_stream_health` 的 msgs/reconnects/last_event_ms 是否在推进；
  3. 采集器进程启动时刻（若刚重启且数据仍在写 ⇒ 支持 (B)）。

用法：python scripts\h550_conn_vs_probe.py
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    dsn = env.get("MARKET_DATABASE_URL") or env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        dsn = dsn.replace(j, "")
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT now(), extract(epoch from now())::float8")
            now, now_e = cur.fetchone()
            print(f"now = {now:%Y-%m-%d %H:%M:%S}  epoch={now_e:.1f}")
            print("=" * 84)
            # 链路表：统一用 epoch 秒做差
            for tbl, expr, unit in (
                    ("asterdex_trades", "max(recv_ts_ns)/1e9", "ns"),
                    ("asterdex_book_ticker", "max(recv_ts_ns)/1e9", "ns"),
                    ("asterdex_depth_snapshots", "max(recv_ts_ns)/1e9", "ns"),
                    ("market_trades_aggregated", "extract(epoch from max(created_at))", "s"),
                    ("market_orderbook_snapshots", "extract(epoch from max(created_at))", "s"),
                    ("asterdex_stream_health", "extract(epoch from max(updated_at))", "s")):
                cur.execute(f"SELECT {expr} FROM {tbl}")  # noqa: S608
                mx = cur.fetchone()[0]
                lag = (now_e - float(mx)) if mx else None
                # 最近 60 秒的行数（按各自的时间列）
                col = ("to_timestamp(recv_ts_ns/1e9)" if unit == "ns" else "created_at")
                if tbl == "asterdex_stream_health":
                    col = "updated_at"
                cur.execute(
                    f"SELECT count(*) FROM {tbl} WHERE {col} > now() - interval '60 seconds'")  # noqa: S608
                n60 = cur.fetchone()[0]
                flag = ""
                if lag is None:
                    flag = "  ← 空"
                elif lag > 300:
                    flag = "  ← **断**"
                elif lag > 90:
                    flag = "  ← 偏慢"
                print(f"  {tbl:<28} 滞后 {lag:8.1f}s   近60s {n60:6d} 行{flag}")
            print("\nasterdex_stream_health 明细（msgs_total 是否在推进）")
            cur.execute("""SELECT stream, msgs_total, reconnects, last_event_ms, updated_at
                           FROM asterdex_stream_health ORDER BY stream""")
            for s, m, r, e, u in cur.fetchall():
                print(f"  {s:<7} msgs={m:<8} reconnects={r:<5} last_event_ms={e} "
                      f"updated={u}")
    print("\n采集器进程（启动时刻）：")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
             "Where-Object { $_.CommandLine -match 'aster_ws_ingest' } | "
             "ForEach-Object { $_.ProcessId.ToString() + ' | ' + "
             "$_.CreationDate.ToString('HH:mm:ss') }"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=90)
        for line in (r.stdout or "").strip().splitlines():
            print("  pid|start = " + line)
    except Exception as e:
        print(f"  （失败：{str(e)[:70]}）")
    print("\n判读：若各表滞后 < 90s 且近 60s 有行 ⇒ **采集健康**（探针失败属 (B)）；"
          "若滞后持续增长 ⇒ (A)，必须立刻换节点。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
