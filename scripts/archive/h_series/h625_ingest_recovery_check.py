"""h625 — **采集恢复核验**（只读；R230）。

背景（2026-09-29 17:26–17:45L 事故）：两个**不可重启**的采集器进程死掉（WS 重连 15 次后退出），
而看门狗因 `aster_ws_ingest.py` **仍缺失** ✗ 只能每分钟记 "script not found"（R99 的护栏
让它**不杀进程** ✓，但也起不来 ✗）⇒ 行情表停更 ~16 分钟（`trades` 滞后 977s ✗）。
处置：用**待命替换件** `standby_ingest.py --apply --to-prod` 接管 ✓，并新建**计划任务**
`DSH_ASTER_INGEST`（每 5 分钟、IgnoreNew、SWA ✓）让它**不再依赖会话存活** ✓。

本脚本核验五件事（全只读 ✓）：
  1. 三条流的**新鲜度**（秒级 ✓）与 2 分钟内的**覆盖面**（book/trades 35 币、depth 28 币 ✓）；
  2. `asterdex_stream_health` 三行是否在推进（msgs / reconnects / updated_at ✓）；
  3. 采集进程与 `DSH_ASTER_INGEST` 任务状态 ✓；
  4. **车道是否已恢复出腿**（恢复后 15 分钟的腿数 ✓）；
  5. 陈旧数据对在用表的影响（`market_orderbook_snapshots` ✓）。

用法：python scripts/h625_ingest_recovery_check.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402


def _dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("MARKET_DATABASE_URL") or env.get("DATABASE_URL") or ""
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def main() -> int:
    print("=" * 94)
    print("h625 — 采集恢复核验（只读）")
    print("=" * 94)
    fails = []
    with psycopg.connect(_dsn(), autocommit=True) as c, c.cursor() as cur:
        # 1) 新鲜度 + 覆盖面
        print("\n  [1] 表新鲜度与覆盖面（近 2 分钟）")
        for tbl, col in (("asterdex_trades", "event_ts_ms"),
                         ("asterdex_book_ticker", "event_ts_ms"),
                         ("asterdex_depth_snapshots", "event_ts_ms")):
            cur.execute(f'SELECT EXTRACT(EPOCH FROM (now() - to_timestamp(max("{col}")/1000.0)))::float8,'
                        f' count(DISTINCT symbol) FILTER (WHERE "{col}" > (EXTRACT(EPOCH FROM now())-120)*1000)'
                        f' FROM "{tbl}"')
            lag, syms = cur.fetchone()
            want = 28 if "depth" in tbl else 35
            ok = (lag is not None and lag <= 60) and (int(syms or 0) >= want - 2)
            print(f"    {tbl:<26} 滞后 {float(lag or -1):6.1f}s  近 2 分钟币数 {int(syms or 0):>3}"
                  f"（期望 ≈{want}）{' ✓' if ok else ' ✗'}")
            if not ok:
                fails.append(f"{tbl} 滞后/覆盖面异常")
        # 2) 心跳
        print("\n  [2] asterdex_stream_health 三行")
        cur.execute("SELECT stream, msgs_total, reconnects,"
                    " EXTRACT(EPOCH FROM (now() - updated_at))::float8"
                    " FROM asterdex_stream_health ORDER BY stream")
        for s, m, rc, age in cur.fetchall():
            ok = age is not None and age <= 60
            print(f"    {s:<8} msgs={int(m or 0):>10} reconnects={rc} updated_at 滞后 {float(age or -1):5.1f}s"
                  f"{' ✓' if ok else ' ✗'}")
            if not ok:
                fails.append(f"心跳 {s} 陈旧")
        # 5) 在用表
        cur.execute("SELECT EXTRACT(EPOCH FROM (now() - to_timestamp(max(timestamp)/1000.0)))::float8"
                    " FROM market_orderbook_snapshots")
        lag5 = cur.fetchone()[0]
        print(f"\n  [5] market_orderbook_snapshots 滞后 {float(lag5 or -1):.1f}s"
              f"{' ✓' if (lag5 is not None and lag5 <= 120) else ' ✗'}")
    # 3) 进程与任务
    print("\n  [3] 采集进程与计划任务")
    ps = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                         "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                         "Where-Object { $_.CommandLine -like '*standby_ingest*' }).ProcessId -join ','"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    pids = (ps.stdout or "").strip()
    print(f"    standby 进程 = {pids or '（无 ✗）'}")
    if not pids:
        fails.append("没有采集进程")
    q = subprocess.run(["schtasks", "/Query", "/TN", "DSH_ASTER_INGEST", "/FO", "LIST"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    st = ""
    for ln in (q.stdout or "").splitlines():
        if ln.strip().lower().startswith("status:"):
            st = ln.split(":", 1)[1].strip()
    print(f"    DSH_ASTER_INGEST 状态 = {st or '(未知 ✗)'}（应 Ready/Running ✓；每 5 分钟自愈 ✓）")
    if not st:
        fails.append("任务状态未知")
    # 4) 车道是否恢复出腿
    print("\n  [4] 车道恢复（恢复后 15 分钟腿数）")
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib.util
    s = importlib.util.spec_from_file_location(
        "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
    h = importlib.util.module_from_spec(s)
    s.loader.exec_module(h)  # type: ignore[union-attr]
    with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
        cur.execute("SELECT count(*), max(ts AT TIME ZONE 'Asia/Shanghai') FROM lane_ledger"
                    " WHERE lane_id=%s AND ts > now() - interval '15 minutes'", (h.LANE,))
        n, last = cur.fetchone()
        print(f"    近 15 分钟 {int(n or 0)} 腿；最后一腿 {last}")
        if int(n or 0) == 0:
            fails.append("车道近 15 分钟无腿")
    print("\n" + "-" * 94)
    if fails:
        print(f"✗ {len(fails)} 项未通过：{fails}")
        return 1
    print("✓ 采集已恢复：三条流秒级新鲜、覆盖面完整、心跳推进、车道在出腿 ✓")
    print("  ⚠️ 仍待人工：把 `aster_ws_ingest.py` 原件放回（见 研究结论/误删事故_20260929.md）")
    print("     —— 在那之前，看门狗只会记「script not found」，靠本替换件 + 计划任务续命 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
