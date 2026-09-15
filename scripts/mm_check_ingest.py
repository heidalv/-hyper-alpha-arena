"""行情落库健康检查（快照 / 成交桶 / 车道 tick），用于"车道是不是瞎了"的快速判定。

[F191 2026-09-15] 为什么需要：做市链路的**唯一**输入是 `market_orderbook_snapshots`
与 `market_trades_aggregated`（F112：盘口不是成交判据，成交流是唯一判据 ✓）。
两表任一断流，车道会"看起来在跑、其实收不到任何东西"✗ —— 而 `/shadow` 的
`win_empty` 与 `skip_counts` 只会变差、不会直说"数据停了" ✗。
本脚本把三件事并排给出：快照最新年龄、成交桶最新年龄、近 10 分钟行数。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from backend.database.connection import MarketSessionLocal  # noqa: E402

SYMS = ["BTC", "ETH", "BNB", "XRP", "SOL"]


def coverage(since_ms: int, until_ms: int, win_ms: int = 15_000) -> None:
    """[F193] 成交桶**网格覆盖率**（= 有行的 15s 桶 / 应有的 15s 桶）。

    为什么要它：F192 那个 `KeyError` 会让**同一批成交里之后的成交丢失** ✗，但表面看
    "数据还在动"（覆盖率仍有 ~93%）⇒ 只有把覆盖率**与修复后对比**才能量化它到底
    削掉了多少。这个数直接决定"修复前跑的参数扫描"要不要重做 ✓。
    """
    expected = max(1, (until_ms - since_ms) // win_ms)
    with MarketSessionLocal() as s:
        s.execute(text("SET statement_timeout = 20000"))
        rows = [dict(r) for r in s.execute(text(
            "SELECT symbol, count(*) AS n FROM market_trades_aggregated"
            " WHERE exchange='asterdex' AND timestamp >= :a AND timestamp < :b"
            " GROUP BY symbol ORDER BY symbol"), {"a": since_ms, "b": until_ms}
        ).mappings().all()]
    print(f"\n成交桶覆盖率 {since_ms}→{until_ms}  应有 {expected} 个 15s 桶/币")
    tot = 0
    for r in rows:
        n = int(r["n"])
        tot += n
        print(f"  {r['symbol']:<6} {n:>5} 桶  覆盖 {100.0*n/expected:5.1f}%")
    if rows:
        print(f"  合计覆盖 {100.0*tot/(expected*len(rows)):5.1f}%")


def main() -> int:
    lane = sys.argv[1] if len(sys.argv) > 1 else "mm_asterdex"
    if "--coverage" in sys.argv:
        # 用法：--coverage <since_iso> <until_iso>
        from datetime import datetime
        i = sys.argv.index("--coverage")
        a = datetime.fromisoformat(sys.argv[i + 1])
        b = datetime.fromisoformat(sys.argv[i + 2])
        a_ms, b_ms = int(a.timestamp() * 1000), int(b.timestamp() * 1000)
        # 关键：窗口末端不能取未来（否则分母把"还没发生的时间"算成缺桶 ✗ —— 第一版
        # 就这样把 15% 的错误覆盖率打出来过）
        b_ms = min(b_ms, int(time.time() * 1000))
        coverage(a_ms, b_ms)
        return 0
    now_ms = int(time.time() * 1000)
    print(f"行情落库体检 {time.strftime('%Y-%m-%d %H:%M:%S')}  车道 {lane}")
    with MarketSessionLocal() as s:
        s.execute(text("SET statement_timeout = 20000"))
        ob = [dict(r) for r in s.execute(text(
            "SELECT symbol, max(timestamp) AS mx, count(*) AS n FROM market_orderbook_snapshots"
            " WHERE timestamp > :c GROUP BY symbol ORDER BY symbol"), {"c": now_ms - 600_000}
        ).mappings().all()]
        tr = [dict(r) for r in s.execute(text(
            "SELECT symbol, max(timestamp) AS mx, count(*) AS n FROM market_trades_aggregated"
            " WHERE exchange = 'asterdex' AND timestamp > :c GROUP BY symbol ORDER BY symbol"),
            {"c": now_ms - 600_000}
        ).mappings().all()]
        # 成交桶的"入库时刻"（可见性）= created_at（naive 本地墙钟 ⇒ 用 now() 同口径比较）
        vis = [dict(r) for r in s.execute(text(
            "SELECT symbol, max(created_at) AS mx FROM market_trades_aggregated"
            " WHERE exchange = 'asterdex' AND created_at > now() - interval '20 minutes'"
            " GROUP BY symbol ORDER BY symbol")).mappings().all()]
    obm = {r["symbol"]: r for r in ob}
    trm = {r["symbol"]: r for r in tr}
    vism = {r["symbol"]: r for r in vis}
    print(f"{'币':<6}{'快照年龄':>10}{'快照/10m':>10}{'成交桶年龄':>12}{'成交桶/10m':>12}{'落库时刻(可见性)':>26}")
    for sym in SYMS:
        o, t, v = obm.get(sym), trm.get(sym), vism.get(sym)
        oa = f"{(now_ms - int(o['mx']))/1000:.0f}s" if o and o["mx"] else "—"
        ta = f"{(now_ms - int(t['mx']))/1000:.0f}s" if t and t["mx"] else "—"
        print(f"{sym:<6}{oa:>10}{(o['n'] if o else 0):>10}{ta:>12}{(t['n'] if t else 0):>12}"
              f"{str(v['mx']) if v else '—':>26}")
    try:
        st = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:8000/api/trading/lanes/{lane}/shadow", timeout=20).read())
        print(f"\n车道: tick={st.get('ticks')} 成交={st.get('fills')} 速率={st.get('fills_per_hour')}/h "
              f"挂宽={st.get('avg_width_bp')} σ={st.get('avg_sigma')} "
              f"空分片={st.get('cross_counts', {}).get('win_empty')}/"
              f"{st.get('cross_counts', {}).get('win_judged')}")
        print(f"闸门: {st.get('skip_counts')}")
        rt = st.get("recent_ticks") or []
        if rt:
            last = rt[-1]
            print(f"最近 tick: { {k: last.get(k) for k in ('symbol','action','bid','ask','jq','wl','wh')} }")
    except Exception as e:
        print(f"⚠ 车道状态读取失败: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
