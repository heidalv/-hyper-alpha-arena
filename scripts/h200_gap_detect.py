# -*- coding: utf-8 -*-
"""H200 数据缺口检测与登记（D1 + D2）。

# 为什么需要它

用户需求："断了的时候能马上在后台补全"。

要"补全"先得"知道缺在哪"。当前**没有任何缺口检测** ——
采集器自身的 `asterdex_stream_health` 只记 `reconnects` 计数，
**不记缺口区间** ⇒ 事后无法回答"哪一段数据不可信"，也就无法让引擎避开它。

# 判据（为什么不是"空桶"）

早期的空桶法（`generate_series + LEFT JOIN`）有两个问题：
  ① 在 1.35 亿行表上**无限期挂住**（对 `event_ts_ms` 做表达式无法走索引）
  ② **空桶 ≠ 断流**：清淡时段本来就没成交（`market_trades_aggregated` 尤其如此）

本脚本改用**相邻行间隔**判据：对每个币，按时间排序求相邻行的间隔，
间隔 > `expected_sec × tolerance` 才算缺口。这直接量的是"数据流断了多久"，
与"有没有成交"无关，且能用 `(symbol, ts)` 索引高效完成。

# 可回补性（gap 表的核心字段）

| 类型 | 能否回补 | 依据 |
|---|---|---|
| `klines` | **能**（REST 历史接口） | 交易所提供历史 K 线 |
| `trades` | **能**（aggTrades 历史接口） | 交易所提供历史成交 |
| `depth` | **不能** | 20 档快照只在 WS 实时推送，无历史接口 |
| `book_ticker` | **不能** | 同上（最优买卖价的历史回不去） |

⇒ `backfillable` 字段让下游知道"这条缺口是可修的还是永久的"。
引擎侧（D3）只应对**不可回补**的缺口做避让，可回补的等补完即可。

# 用法

    python scripts/h200_gap_detect.py                       # 检测并登记（近 6h）
    python scripts/h200_gap_detect.py --hours 48            # 更长的窗口
    python scripts/h200_gap_detect.py --table asterdex_book_ticker --symbols ASTERUSDT
    python scripts/h200_gap_detect.py --report              # 只看已有缺口，不重新扫描
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]

# (表, 时间列(ms), 期望行间隔秒, 能否 REST 回补, 是否"稀疏容忍", 消费者)
#
# `sparse_ok=True` 的表：行只在**有事件**时产生（成交表），
# 所以"长时间没有行"多半只是**清淡无成交**，不一定是断流。
# 这类缺口登记为 `severity='info'`，**不参与引擎避让**；
# 盘口/深度/快照表则是持续推送的，缺行基本可判定为断流 ⇒ `severity='warn'`。
#
# 为什么必须分开：不分的话 `asterdex_trades` 会报出 4240 条"缺口"、
# 合计 42 万秒，绝大多数是凌晨清淡时段的正常空档 ⇒ **告警疲劳**，
# 真正要紧的那 3 条（ONDO 断了 88 分钟）会被淹没。
SPECS = [
    ("asterdex_book_ticker", "event_ts_ms", 1.0, False, False,
     "选币器候选池 / 点差统计"),
    ("asterdex_depth_snapshots", "event_ts_ms", 1.0, False, False,
     "深度 / 队列感知模拟"),
    ("asterdex_trades", "event_ts_ms", 5.0, True, True,
     "活跃度代理（稀疏：无成交即无行）"),
    ("market_orderbook_snapshots", "timestamp", 20.0, False, False,
     "**引擎 fetch_market / backfill_mid_hist（关键）**"),
    ("market_trades_aggregated", "timestamp", 15.0, True, True,
     "**引擎成交桶（关键，但稀疏）**"),
]

# 间隔超过 `expected × MULT` 才算缺口。
# 取 6：比正常抖动（WS 延迟、交易所推送节奏）宽得多，避免把噪声登记成缺口；
# 又会抓到任何 >= ~1 分钟的真实断流（对 20s 快照表）。
GAP_MULT = 6.0


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
    base, _, _ = url.rpartition("/")
    return f"{base}/alpha_market"


DDL = """
CREATE TABLE IF NOT EXISTS market_data_gaps (
    id            BIGSERIAL PRIMARY KEY,
    table_name    TEXT        NOT NULL,
    symbol        TEXT,
    gap_start_ms  BIGINT      NOT NULL,
    gap_end_ms    BIGINT      NOT NULL,
    gap_sec       DOUBLE PRECISION NOT NULL,
    backfillable  BOOLEAN     NOT NULL DEFAULT FALSE,
    severity      TEXT        NOT NULL DEFAULT 'warn',
    detected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    note          TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_mdg_key
    ON market_data_gaps (table_name, COALESCE(symbol, ''), gap_start_ms);
CREATE INDEX IF NOT EXISTS ix_mdg_start ON market_data_gaps (gap_start_ms DESC);
CREATE INDEX IF NOT EXISTS ix_mdg_table ON market_data_gaps (table_name, gap_start_ms DESC);
CREATE INDEX IF NOT EXISTS ix_mdg_sev ON market_data_gaps (severity, gap_start_ms DESC);
"""


def ensure_table(cur) -> None:
    for stmt in DDL.strip().split(";"):
        s = stmt.strip()
        if s:
            cur.execute(s)


def detect(table: str, col: str, expected_sec: float, hours: float,
           symbols: list | None, *, mult: float = 6.0,
           max_symbols: int = 60, adaptive: bool = True) -> list:
    """返回 `[(symbol, start_ms, end_ms, gap_sec, thresh_sec)]`。

    **逐币循环**而不是一次全表窗口函数：
    表的索引是 `(symbol, event_ts_ms)`，逐币查询能走索引，
    而全表 `lag()` 会退化成全扫（1.35 亿行 ⇒ 分钟级甚至更久）。
    本仓库有同类教训：`= ANY(...)` 在大表上 134s，逐币 `LIMIT 1` 只要 8ms。

    `adaptive=True` 时**按币校准阈值**（推荐）：
    冷门币（WLFI/XLM/LTC）本身就不是每秒推送，用统一的 6s 阈值会把
    它们的正常稀疏判成缺口。实测统一阈值下的 3869 条 warn 里
    **98.5% 落在 6–21s**、且长缺口全是单币孤立事件（每条只涉及 1 个币）
    ⇒ 全是噪声，会淹没真正要紧的 3 条（ONDO 断 88 分钟）。
    自适应阈值 = `max(expected×mult, 该币间隔中位数 × mult)`。
    """
    import psycopg
    since_ms = int((time.time() - hours * 3600) * 1000)
    gaps = []
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SET statement_timeout = 180000")
            if not symbols:
                cur.execute(f"SELECT DISTINCT symbol FROM {table} WHERE {col} > %s"
                            f" ORDER BY symbol LIMIT %s", (since_ms, max_symbols))
                symbols = [r[0] for r in cur.fetchall()]

            # ── 每币的间隔中位数（一次查询算完，避免逐币再扫两遍）──
            base = {}
            if adaptive:
                cur.execute(
                    f"""
                    WITH s AS (
                      SELECT symbol, {col} AS ts FROM {table}
                      WHERE {col} > %s ORDER BY symbol, {col}
                    ), d AS (
                      SELECT symbol, (ts - lag(ts) OVER (PARTITION BY symbol
                                                         ORDER BY ts)) / 1000.0 AS g
                      FROM s
                    )
                    SELECT symbol,
                           percentile_cont(0.5) WITHIN GROUP (ORDER BY g) AS med,
                           count(*) AS n
                    FROM d WHERE g IS NOT NULL AND g >= 0
                    GROUP BY symbol
                    """, (since_ms,))
                for sym, med, n in cur.fetchall():
                    if med is not None and int(n or 0) >= 20:
                        base[sym] = float(med)

            for sym in symbols:
                thresh = expected_sec * mult
                if adaptive and sym in base:
                    thresh = max(thresh, base[sym] * mult)
                cur.execute(
                    f"""
                    WITH s AS (
                      SELECT {col} AS ts
                      FROM {table}
                      WHERE symbol = %s AND {col} > %s
                      ORDER BY {col}
                    ), d AS (
                      SELECT ts, lag(ts) OVER (ORDER BY ts) AS prev FROM s
                    )
                    SELECT prev, ts, (ts - prev) / 1000.0 AS gap_s
                    FROM d
                    WHERE prev IS NOT NULL AND (ts - prev) / 1000.0 > %s
                    ORDER BY prev
                    """, (sym, since_ms, thresh))
                for prev, nxt, g in cur.fetchall():
                    gaps.append((sym, int(prev), int(nxt), float(g), thresh))
    return gaps


def classify_by_concurrency(gaps: list, window_ms: int = 3000,
                            min_symbols: int = 3,
                            active_pairs: set | None = None) -> list:
    """按"是否多币同时缺"+"该币当时是否活跃"分级。

    # 判据一：多币同时缺 = 采集侧问题

    · **多币在同一时刻缺** ⇒ 采集侧问题（WS 断流 / 进程重启 / DB 卡住）
      ⇒ `severity='warn'`
    · **单币孤立缺** ⇒ 多数情况是交易所对该币推送稀疏（冷门币本来就不是每秒推）
      ⇒ `severity='info'`

    # 为什么不用阈值

    实测统一 6s 阈值下 `asterdex_book_ticker` 报出 3836 条"缺口"，
    其中 98.5% 落在 6–21s；改成"按币间隔中位数自适应"后**几乎没减少**
    —— 因为这些币的中位间隔本来就 <1s，尾部却常有几十秒空档，
    多倍阈值照样命中。继续调阈值只是把噪声挪个位置。

    而并发判据直接区分两种物理成因，实测：
      · `23:27:38→23:27:48`  WLD/ADA/XMR/SOLUSD1 **同时**缺 10s（13 个币）
        ⇒ 那正是重启采集进程的时刻（全局事件）⇒ 全部 warn ✓
      · `WLFIUSDT 18:41:53→18:43:26` 缺 93s，**只涉及它自己** ⇒ info ✓

    # 判据二：单币孤立但"该币当时活跃"= 也要 warn

    首版只看并发，把 **ONDO 缺 88 分钟（21:26→22:54）判成了 info** ——
    因为它是单币孤立。但那明显是采集中断：它在缺之前和之后都在正常出数据。
    单币孤立的成因有两种，必须分开：
      · 该币**一直很清淡**（整段窗口都没几行）⇒ 正常 ⇒ info
      · 该币**当时在正常出数据，中间断了** ⇒ 采集中断 ⇒ **warn**

    判据：缺口起点前 `PRE_MS` 内有数据 **且** 终点后 `PRE_MS` 内有数据
    ⇒ 该币当时是活跃的、只是中间断了。
    """
    import bisect
    starts = sorted(g[1] for g in gaps)
    out = []
    for g in gaps:
        sym, a, b, sec, _th = g
        lo = bisect.bisect_left(starts, a - window_ms)
        hi = bisect.bisect_right(starts, a + window_ms)
        n_sym = len({gaps[i][0] for i in range(lo, hi)})

        severity = "warn" if n_sym >= min_symbols else "info"
        reason = f"多币同时缺（{n_sym} 币）" if severity == "warn" else "单币孤立"

        # 判据二：单币孤立但该币当时活跃 ⇒ 仍然是采集中断
        if severity == "info" and active_pairs is not None:
            if (sym, "before") in active_pairs and (sym, "after") in active_pairs:
                severity = "warn"
                reason = "单币孤立但该币当时活跃（缺前缺后都有数据）⇒ 采集中断"

        out.append({"gap": g, "concurrent_symbols": n_sym,
                    "severity": severity, "reason": reason})
    return out


# 哪些表要做"该币当时是否活跃"的二次判定（判据二）。
#
# 只对**引擎真正依赖**的表做。理由：
#   · `market_orderbook_snapshots` / `market_trades_aggregated` 是引擎
#     `fetch_market` 与 `backfill_mid_hist` 的输入 —— ONDO 缺 88 分钟
#     必须被识别为 warn，否则引擎会拿残缺的 mid_hist 决策
#   · `asterdex_book_ticker` 有 3800+ 条缺口，逐条跑 `EXISTS` 极慢，
#     而且它**没有引擎消费者**（只喂选币器与统计）
#   · `asterdex_trades` 是稀疏表，一律 info
ACTIVE_CHECK_TABLES = {"market_orderbook_snapshots", "market_trades_aggregated"}


def active_around_gaps(table: str, col: str, gaps: list,
                       pre_ms: int = 300_000) -> set:
    """判断每个缺口两侧该币是否活跃 ⇒ 返回 `{(symbol,'before'|'after')}`。

    "活跃"= 缺口起点前 `pre_ms` 内有数据，或终点后 `pre_ms` 内有数据。
    只用两个 `EXISTS` 式查询（走 `(symbol, ts)` 索引），逐币一次。
    """
    import psycopg
    pairs: set = set()
    by_sym: dict = {}
    for g in gaps:
        by_sym.setdefault(g[0], []).append(g)
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SET statement_timeout = 180000")
            for sym, gs in by_sym.items():
                for _s, a, b, _sec, _th in gs:
                    cur.execute(
                        f"SELECT EXISTS(SELECT 1 FROM {table} WHERE symbol=%s"
                        f" AND {col} <= %s AND {col} >= %s)",
                        (sym, a, a - pre_ms))
                    if cur.fetchone()[0]:
                        pairs.add((sym, "before"))
                    cur.execute(
                        f"SELECT EXISTS(SELECT 1 FROM {table} WHERE symbol=%s"
                        f" AND {col} >= %s AND {col} <= %s)",
                        (sym, b, b + pre_ms))
                    if cur.fetchone()[0]:
                        pairs.add((sym, "after"))
    return pairs


def record(gaps: list, table: str, backfillable: bool, *,
           severity: str = "warn", note: str = "") -> int:
    """幂等写入（唯一键 = 表+币+起点），返回新增条数。"""
    if not gaps:
        return 0
    import psycopg
    n = 0
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            ensure_table(cur)
            for sym, a, b, g, _th in gaps:
                cur.execute(
                    "INSERT INTO market_data_gaps"
                    " (table_name, symbol, gap_start_ms, gap_end_ms, gap_sec,"
                    "  backfillable, severity, note)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
                    " ON CONFLICT (table_name, COALESCE(symbol,''), gap_start_ms)"
                    " DO NOTHING",
                    (table, sym, a, b, g, backfillable, severity, note))
                n += cur.rowcount
        c.commit()
    return n


def report(only_unfilled: bool = True) -> None:
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            ensure_table(cur)
            cur.execute("""
                SELECT table_name, backfillable, severity, count(*),
                       min(gap_start_ms), max(gap_end_ms), sum(gap_sec)
                FROM market_data_gaps GROUP BY 1,2,3 ORDER BY 4 DESC
            """)
            rows = cur.fetchall()
    print("\n  ── 已登记缺口汇总 ──")
    if not rows:
        print("    （无缺口）")
        return
    print(f"    {'表':<32}{'级别':>6}{'可回补':>7}{'条数':>7}{'总缺口秒':>12}  区间")
    print("    " + "-" * 84)
    for t, bf, sev, n, a, b2, tot in rows:
        fa = datetime.fromtimestamp(a / 1000).strftime("%m-%d %H:%M")
        fb = datetime.fromtimestamp(b2 / 1000).strftime("%m-%d %H:%M")
        print(f"    {t:<32}{sev:>6}{'是' if bf else '否':>7}{n:>7}{float(tot or 0):>12.0f}"
              f"  {fa} → {fb}")
    print("\n    级别说明：`warn` = 引擎应避让（持续推送的表缺行 ⇒ 断流）；")
    print("              `info` = 稀疏表（成交类），长时间无行多半只是清淡无成交。")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--table", default="")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--mult", type=float, default=GAP_MULT)
    ap.add_argument("--report", action="store_true", help="只汇报已有缺口")
    ap.add_argument("--dry-run", action="store_true")
    # [F333] 用途分工：关键表（引擎输入）高频细扫，其余表低频粗扫。
    # 实测全 5 张表跑一遍 >5 分钟，其中 `asterdex_book_ticker`（1.35 亿行）
    # 的逐币循环就占 100s+，而它**没有引擎消费者**。
    # 挂成每小时任务时，把时间花在引擎真正依赖的两张表上才有意义。
    ap.add_argument("--profile", default="all",
                    choices=["all", "engine", "aux"],
                    help="engine=只扫引擎关键两表（快，适合每小时）；"
                         "aux=只扫其余三表（慢，适合每天）；all=全部")
    a = ap.parse_args()

    mult = float(a.mult)
    thresh_txt = ", ".join(f"{s[0]}>{s[2]*mult:.0f}s" for s in SPECS[:2])

    print("=" * 96)
    print("H200  数据缺口检测与登记")
    print("=" * 96)
    print(f"  窗口 {a.hours:.0f}h   判据 = 相邻行间隔 > 期望间隔 × {mult:g}"
          f"（例：{thresh_txt}）")

    if a.report:
        report()
        return 0

    syms = [s.strip() for s in a.symbols.split(",") if s.strip()] or None
    specs = [s for s in SPECS if (not a.table or s[0] == a.table)]
    if a.profile == "engine":
        specs = [s for s in specs if s[0] in ACTIVE_CHECK_TABLES]
    elif a.profile == "aux":
        specs = [s for s in specs if s[0] not in ACTIVE_CHECK_TABLES]
    print(f"  profile={a.profile} ⇒ 扫描 {len(specs)} 张表")
    total_new = 0
    for table, col, exp, bf, sparse_ok, who in specs:
        t0 = time.time()
        try:
            gaps = detect(table, col, exp, a.hours, syms, mult=mult)
        except Exception as e:
            print(f"\n  {table}\n    ✗ 检测失败: {type(e).__name__}: {str(e)[:100]}")
            continue
        dt = time.time() - t0
        if not gaps:
            print(f"\n  {table}\n    无缺口（判据 >{exp*mult:.0f}s）  [{dt:.1f}s]")
            continue

        # 分级：稀疏表一律 info；持续推送的表按"多币并发 + 该币当时是否活跃"分级
        if sparse_ok:
            cls = [{"gap": g, "concurrent_symbols": 0, "severity": "info",
                    "reason": "稀疏表（无成交即无行）"} for g in gaps]
        else:
            pairs = None
            if table in ACTIVE_CHECK_TABLES:
                try:
                    pairs = active_around_gaps(table, col, gaps)
                except Exception as e:
                    print(f"    ⚠️ 活跃度判定失败（{type(e).__name__}: {str(e)[:60]}）"
                          f"，退回仅按并发分级")
            cls = classify_by_concurrency(gaps, active_pairs=pairs)
        warn = [c for c in cls if c["severity"] == "warn"]
        info = [c for c in cls if c["severity"] == "info"]
        worst = max(g[3] for g in gaps)
        tot = sum(g[3] for g in gaps)
        print(f"\n  {table}   ({who})")
        print(f"    缺口 {len(gaps)} 条（warn {len(warn)} / info {len(info)}）"
              f"  最长 {worst:.0f}s  合计 {tot:.0f}s"
              f"  可回补={'是' if bf else '否'}  [{dt:.1f}s]")
        for c in sorted(warn, key=lambda r: -r["gap"][3])[:6]:
            sym, x, y, g, _th = c["gap"]
            fa = datetime.fromtimestamp(x / 1000).strftime("%H:%M:%S")
            fb = datetime.fromtimestamp(y / 1000).strftime("%H:%M:%S")
            print(f"      [warn] {sym:<13} {fa} → {fb}  {g:>7.0f}s   {c['reason']}")
        if len(warn) > 6:
            print(f"      …另有 {len(warn)-6} 条 warn")
        if not warn and info:
            c = max(info, key=lambda r: r["gap"][3])
            sym, x, y, g, _th = c["gap"]
            fa = datetime.fromtimestamp(x / 1000).strftime("%H:%M:%S")
            fb = datetime.fromtimestamp(y / 1000).strftime("%H:%M:%S")
            print(f"      （无 warn；最长 info：{sym} {fa}→{fb} {g:.0f}s，"
                  f"{c['reason']}）")

        if not a.dry_run:
            n = 0
            for c in cls:
                n += record([c["gap"]], table, bf, severity=c["severity"],
                            note=f"{who} | {c['reason']}")
            total_new += n
            print(f"    已登记新增 {n} 条")

    if not a.dry_run:
        print(f"\n  本次新增登记 {total_new} 条")
        report()
    else:
        print("\n  （--dry-run：未写库）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
