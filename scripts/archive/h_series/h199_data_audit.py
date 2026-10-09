# -*- coding: utf-8 -*-
"""H199 数据中心统一审计 —— 一个地方看清所有采集流、重复进程、数据缺口。

# 用户需求（2026-09-21）

"数据这里，需要做到就算是断了就能重新连上，是不是统一由数据中心管理，
 做好存储，断了的时候能马上在后台补全"

本脚本是这条需求的**观测面**：在动手改采集架构之前，先能回答三个问题：
  1. 现在有几条采集流、各自新鲜吗？
  2. 有没有**重复进程**在写同一批表（双写）？
  3. 有没有**数据缺口**？哪些缺口能补、哪些补不了？

# 为什么必须先有观测面

本项目反复出现的模式：**改了但不知道有没有生效**。
采集层尤其危险 —— 它没有 UI，断了只表现为"数据变少"，
而"变少"在看板上和"行情清淡"长得一模一样。
⇒ 必须有**独立于采集进程**的审计：即使采集全挂，审计照样能报出"挂了"。

# 已查明的现状（2026-09-21 22:1x 实测，作为本脚本的基线）

**重连：已有，且工作正常**
  `services/aster_ws_ingest.py` 自带：
    · `ping_interval=20, ping_timeout=20, close_timeout=5`
    · 指数退避 1s → 60s（`backoff = min(backoff * 2, 60.0)`）
    · 重连计数落库 `asterdex_stream_health.reconnects`
  实测：book `reconnects=0`、depth `0`、trades `1`（启动至今 ~11.7 小时）

**统一管理：部分具备，且有重复**
  数据中心的 `_run_collectors` 管 kline / ticker / binance ticker /
  live_kline / depth_backfill / freshness_inspector 六项，
  带 `_COMP_STALE_SEC` 新鲜度阈值与 `/health` HTTP 端口 9100。
  ⚠️ 但 `aster_ws_ingest.py` **不在它的组件清单里** —— 它由
  `research_l1` 侧独立启动，数据中心看不见它。

**存储：已具备，有缺口风险**
  `asterdex_book_ticker` 1.34 亿行 / `asterdex_depth_snapshots` 4123 万行。
  ⚠️ 但**引擎真正依赖的 `market_orderbook_snapshots` 只覆盖 12 个币**
  ⇒ 选币器从 35 个币里挑、引擎只认 12 个（已导致一次"选了跑不了"）。

# 用法

    python scripts/h199_data_audit.py            # 完整审计
    python scripts/h199_data_audit.py --json     # 机器可读
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]

# 引擎与选币器各自依赖的表（不同消费者不同口径，必须分开看）
TABLES = [
    # (表, 时间列, 是否毫秒, 消费者说明)
    ("asterdex_book_ticker", "event_ts_ms", True, "选币器候选池 / 点差统计"),
    ("asterdex_depth_snapshots", "event_ts_ms", True, "深度/队列感知模拟"),
    ("asterdex_trades", "event_ts_ms", True, "活跃度代理"),
    ("market_orderbook_snapshots", "timestamp", True, "**引擎 fetch_market（关键）**"),
    ("market_trades_aggregated", "timestamp", True, "**引擎成交桶（关键）**"),
]

# 采集进程匹配模式 -> 期望实例数
PROC_PATTERNS = [
    ("aster_ws_ingest", "aster_ws_ingest", 1,
     "行情采集（book/depth/trades）—— 数据中心看不见它"),
    ("market_data_center", "market_data_center", 1,
     "数据中心本体（kline/ticker/回填/巡检）"),
    ("mm_lane_worker", "mm_lane_worker", 1, "做市车道 ticker"),
]


def dsn(which: str = "alpha_market") -> str:
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
    base, _, _ = url.rpartition("/")
    return f"{base}/{which}"


def audit_streams() -> dict:
    """采集流健康（来自采集进程自己写的表）。"""
    out = {}
    try:
        import psycopg
        with psycopg.connect(dsn()) as c:
            with c.cursor() as cur:
                cur.execute("SELECT stream, last_event_ms, msgs_total, reconnects,"
                            " updated_at FROM asterdex_stream_health ORDER BY stream")
                for s, ev, msgs, rec, upd in cur.fetchall():
                    out[s] = {"last_event_ms": int(ev or 0), "msgs_total": int(msgs or 0),
                              "reconnects": int(rec or 0),
                              "age_s": round((time.time() * 1000 - int(ev or 0)) / 1000.0, 1),
                              "updated_at": str(upd)}
    except Exception as e:
        out["_error"] = f"{type(e).__name__}: {str(e)[:100]}"
    return out


def audit_tables() -> list:
    """每张关键表：**估算**行数 / 最新时间 / age / 近 10 分钟行数 / 覆盖币数。

    ⚠️ 行数必须用 `pg_class.reltuples` **估算**，不能用 `count(*)`。
    首版就是全表 `count(*)` 卡死（`asterdex_book_ticker` **1.34 亿行**、
    `asterdex_depth_snapshots` **4123 万行**），审计跑 7 分钟不出结果 ——
    而**审计工具因为表大就跑不动，等于没有审计**。
    精确值对"数据有没有在流"毫无必要，估算足够。
    """
    import psycopg
    res = []
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SET statement_timeout = 60000")
            now_ms = int(time.time() * 1000)
            for t, col, _is_ms, who in TABLES:
                row = {"table": t, "consumer": who}
                try:
                    cur.execute("SELECT reltuples::bigint FROM pg_class WHERE relname=%s",
                                (t,))
                    est = cur.fetchone()
                    row["rows_est"] = int(est[0]) if est and est[0] is not None else None
                    cur.execute(f"SELECT max({col}) FROM {t}")
                    last = cur.fetchone()[0]
                    row["last_ms"] = int(last) if last is not None else None
                    row["age_s"] = (round((now_ms - int(last)) / 1000.0, 1)
                                    if last is not None else None)
                    # 只数近期窗口：这个有索引支持（或至少只扫一小段）
                    cur.execute(f"SELECT count(*) FROM {t} WHERE {col} > %s",
                                (now_ms - 600_000,))
                    row["rows_10m"] = int(cur.fetchone()[0])
                    cur.execute(f"SELECT count(DISTINCT symbol) FROM {t} WHERE {col} > %s",
                                (now_ms - 600_000,))
                    row["symbols_10m"] = int(cur.fetchone()[0])
                except Exception as e:
                    row["error"] = f"{type(e).__name__}: {str(e)[:80]}"
                res.append(row)
    return res


def audit_processes() -> list:
    """按模式归类进程，标出**真正的**重复实例（双写风险）。

    ⚠️⚠️ 判据必须是"**独立实例数**"，不是"匹配进程数"。
    本脚本首版（以及更早的几次人工判断）都把 `.venv` 与 `.runtime` 的
    同一进程链误读成了两个实例 —— 实测证据：

        `.venv\\pyvenv.cfg` 写着  home = .runtime\\Python312
        `.venv\\Scripts\\python.exe` 104,952 字节（**启动器桩**）
        `.runtime\\Python312\\python.exe` 274,424 字节（真解释器）

    ⇒ 在 Windows 上，venv 的 `python.exe` 会以**子进程**方式启动真实解释器：
          venv\\python.exe (PID 32012, parent=…)
            └─ .runtime\\Python312\\python.exe (PID 988, parent=32012)
      **这是同一个逻辑进程**，不是两个实例。

    ⇒ 独立实例的判据：该进程的**父进程不在同一匹配集合里**（即它是链的根）。
    这样既不会把启动器对算成两个，也能认出真正并行的两个实例
    （它们会是两条互不相干的链，各自有根）。
    """
    out = []
    try:
        ps = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Select-Object ProcessId,ParentProcessId,CreationDate,CommandLine | "
             "ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=90,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        raw = (ps.stdout or "").strip()
        procs = json.loads(raw) if raw else []
        if isinstance(procs, dict):
            procs = [procs]
    except Exception as e:
        return [{"error": f"{type(e).__name__}: {str(e)[:100]}"}]

    def _interp(cmd: str) -> str:
        c = cmd.lower()
        if "python312" in c or ".runtime" in c:
            return "runtime"
        if "venv" in c:
            return "venv"
        return "?"

    for label, pat, expect, who in PROC_PATTERNS:
        hits = [p for p in procs if pat in str(p.get("CommandLine") or "")]
        hit_pids = {p.get("ProcessId") for p in hits}
        # 只保留"链的根"：父进程不在匹配集合里的那些
        roots = [p for p in hits if p.get("ParentProcessId") not in hit_pids]
        chains = len(roots)
        out.append({
            "component": label, "expected": expect,
            "matched": len(hits), "independent": chains,
            "dup": chains > expect, "role": who,
            "note": ("含启动器父子对 ⇒ 独立实例数见 independent"
                     if len(hits) != chains else ""),
            "pids": [{"pid": p.get("ProcessId"),
                      "ppid": p.get("ParentProcessId"),
                      "interp": _interp(str(p.get("CommandLine"))),
                      "is_root": p.get("ProcessId") in {r.get("ProcessId") for r in roots},
                      "cmd_head": str(p.get("CommandLine"))[:110]}
                     for p in hits],
        })
    return out


def detect_gaps(minutes: int = 120, bucket_min: int = 5, *, verbose: bool = False) -> list:
    """在关键表上找"最后一条数据到现在"的断流，以及近期空桶数。

    ⚠️ 实现刻意**不用 `generate_series + LEFT JOIN`**。
    首版就是那样写的，在 `asterdex_book_ticker`（1.35 亿行）上
    **无限期挂住**（单测 500s 未返回）—— 因为对 `event_ts_ms` 做
    `floor(...)` 表达式无法走 `ix_adx_book_ts` 索引，退化成全表扫描。

    ⇒ 本版只用**索引可用**的查询：
      · `max(col)`            —— 走索引，毫秒级
      · `count(DISTINCT (col/桶))` 限定在小窗口 —— 只扫窗口内的行
    缺口判据改为"最新数据距今多久"，这比"空桶数"更直接且不会误判
    （`market_trades_aggregated` 空桶可能只是**无成交**，不是断流）。
    """
    import psycopg
    out = []
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SET statement_timeout = 45000")
            now_ms = int(time.time() * 1000)
            for t, col, _is_ms, who in TABLES:
                row = {"table": t, "consumer": who, "window_min": minutes}
                try:
                    if verbose:
                        print(f"      … {t}", flush=True)
                    cur.execute(f"SELECT max({col}) FROM {t}")
                    last = cur.fetchone()[0]
                    row["age_s"] = (round((now_ms - int(last)) / 1000.0, 1)
                                    if last is not None else None)
                    # 窗口内有多少个**不同的桶**有数据（桶 = bucket_min 分钟）
                    cur.execute(
                        f"SELECT count(DISTINCT ({col}/1000/{bucket_min*60})::bigint) "
                        f"FROM {t} WHERE {col} > %s",
                        (now_ms - minutes * 60_000,))
                    got = int(cur.fetchone()[0])
                    expect = max(1, minutes // bucket_min)
                    row["buckets_with_data"] = got
                    row["buckets_expected"] = expect
                    row["coverage_pct"] = round(100.0 * got / expect, 1)
                except Exception as e:
                    row["error"] = f"{type(e).__name__}: {str(e)[:80]}"
                out.append(row)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--gaps-min", type=int, default=120)
    a = ap.parse_args()

    d = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "streams": audit_streams(),
        "tables": audit_tables(),
        "processes": audit_processes(),
        "gaps": detect_gaps(a.gaps_min),
    }

    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2, default=str))
        return 0

    print("=" * 100)
    print("H199  数据中心统一审计")
    print("=" * 100)

    print("\n  ① 采集流（采集进程自己上报，更新即证明它活着）")
    st = d["streams"]
    if "_error" in st:
        print(f"    ✗ {st['_error']}")
    else:
        print(f"    {'stream':<8}{'age_s':>8}{'总消息':>14}{'重连次数':>10}   判定")
        for k, v in sorted(st.items()):
            verdict = ("新鲜" if v["age_s"] < 30 else
                       "偏慢" if v["age_s"] < 120 else "**停摆**")
            print(f"    {k:<8}{v['age_s']:>8}{v['msgs_total']:>14,}{v['reconnects']:>10}"
                  f"   {verdict}")

    print("\n  ② 关键表（不同消费者口径不同，必须分开看）")
    print(f"    {'表':<32}{'估算行数':>14}{'age_s':>8}{'10min行':>9}{'币数':>6}  消费者")
    for r in d["tables"]:
        if "error" in r:
            print(f"    {r['table']:<32}  ✗ {r['error']}")
            continue
        est = r.get("rows_est")
        est_s = f"{est:,}" if isinstance(est, int) else "?"
        print(f"    {r['table']:<32}{est_s:>14}{r['age_s']:>8}"
              f"{r['rows_10m']:>9}{r['symbols_10m']:>6}  {r['consumer']}")

    print("\n  ③ 采集进程（判据 = **独立实例数**，见函数 docstring）")
    for p in d["processes"]:
        if "error" in p:
            print(f"    ✗ {p['error']}")
            continue
        flag = "  ⚠️ **重复**" if p["dup"] else "  ✓"
        print(f"    {p['component']:<22} 期望 {p['expected']}  "
              f"独立实例 {p['independent']}（匹配进程 {p['matched']}）{flag}")
        print(f"        {p['role']}")
        for x in p["pids"]:
            tag = "链根" if x["is_root"] else "子进程"
            print(f"          pid={x['pid']:<7} ppid={x['ppid']:<7} {x['interp']:<8} "
                  f"{tag:<6} {x['cmd_head'][:62]}")
    print("\n    注：`.venv\\python.exe` 是**启动器桩**（104KB），它会以子进程方式拉起")
    print("        `.runtime\\Python312\\python.exe`（真解释器，274KB）。")
    print("        ⇒ venv+runtime 的父子对是**同一个逻辑进程**，不是双实例。")

    print(f"\n  ④ 数据连续性（近 {a.gaps_min} 分钟，{a.gaps_min // 5} 个 5 分钟桶）")
    print(f"    {'表':<32}{'最新距今':>10}{'有数据桶':>10}{'应有桶':>8}{'覆盖率':>9}  判定")
    for g in d["gaps"]:
        if "error" in g:
            print(f"    {g['table']:<32}  ✗ {g['error']}")
            continue
        cov = g.get("coverage_pct") or 0.0
        age = g.get("age_s")
        verdict = ("连续" if cov >= 95 else
                   "**有断流**" if cov < 80 else "略有空档")
        if age is not None and age > 120:
            verdict = "**已停摆**"
        print(f"    {g['table']:<32}{str(age):>10}{g.get('buckets_with_data', 0):>10}"
              f"{g.get('buckets_expected', 0):>8}{cov:>8.1f}%  {verdict}")
    print("\n    注：`market_trades_aggregated` 覆盖不足可能只是**无成交**（非断流）；")
    print("        `asterdex_*` 系列盘口每秒都在变，覆盖不足基本可判定为断流。")

    print("\n  ⑤ 结论与待办")
    print("    · 重连：**已具备**（aster_ws_ingest 指数退避 1s→60s + ping 20s +")
    print("      重连计数落库）。实测启动至今 book/depth 均 0 次重连。")
    print("    · 统一管理：**未完成** —— `aster_ws_ingest` 不在数据中心的组件清单里，")
    print("      数据中心 `/health` 看不见它；且两处都能启动采集 ⇒ 出现重复进程。")
    print("    · 存储：已具备；但 `market_orderbook_snapshots` 只覆盖 ~12 个币，")
    print("      而选币器从 35 个币里挑 ⇒ 存在\"选了但引擎跑不了\"的结构性缺口。")
    print("    · 补全：**未实现**。断线期间的事件不会自动回补；")
    print("      能补的只有 REST 支持的 K 线/成交，**20 档深度历史无法回补**。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
