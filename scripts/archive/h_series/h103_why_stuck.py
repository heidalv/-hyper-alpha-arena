"""H103：剩下 ~10% 强平的仓位，到底「卡」在哪里？—— 决定有没有第三种出库机制。

# 背景

H102：打平需强平率 ≤ **1.16%**，当前 **10.6%**，差 9.2 倍。
H97：被动出库 ≤120s 覆盖 98.5%、≤300s 覆盖 99.6%
  ⇒ 上限只多 1.1 个百分点 ⇒ 剩下 ~10% 似乎"等不到"。

**⇒ 若真的"等不到"，就没有第三种出库机制；若能等到，说明该结论错了。**

# 核心问题

对含强平的周期，回看真实 mid 路径：**mid 是否曾回到入场价**（即可不亏离场）？

  · mid 曾回到 ⇒ 说明是"我们没挂住/没等到"，第三种机制有空间
  · mid 从未回到 ⇒ 那些仓位**物理上无法不亏离场** ⇒ 强平必然，地板成立

# 实现（首版失败的教训）

首版把整币的 book_ticker（200 万行）缓存进内存后按币查，结果 150/150 都"无盘口数据"。
⇒ 改为**按 (symbol, 时间窗) 定向查询**：每个周期只取它自己那 300s 的 tick，
既避免大内存，也避免缓存路径上的静默失败（并且失败会被计数看见）。

用法：
    .venv\\Scripts\\python.exe scripts\\h103_why_stuck.py
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

H = 300          # 观察窗 300s（用户给定窗口上限）
MAX_EP = 120     # 最多检查多少个含强平周期（查询成本控制）


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main():
    import numpy as np
    import psycopg2
    import psycopg2.extras

    from h84_derive_episodes import derive, load

    print("=" * 96)
    print("H103  剩下 ~10% 的强平仓位「卡」在哪里？")
    print("=" * 96)

    rows = load()
    eps = derive(rows)
    flat = [e for e in eps if e["flat"]]
    print(f"\n  含强平周期 {len(flat)} / 总周期 {len(eps)}")
    if not flat:
        print("  无强平周期")
        return 0

    by_key = {}
    for r in rows:
        by_key.setdefault((r.get("symbol"), r.get("ts")), r)

    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    checked = 0
    reach = 0
    reach_plus = 0
    never = 0
    reach_ticks = []
    n_norec = n_badpx = n_noob = 0
    checked_syms = Counter()

    for e in flat[:MAX_EP]:
        r0 = by_key.get((e["sym"], e.get("ts0")))
        if r0 is None:
            n_norec += 1
            continue
        px = float(r0.get("fill_px") or 0)
        ts0 = float(r0.get("ts") or 0)
        if px <= 0 or ts0 <= 0:
            n_badpx += 1
            continue
        vs = e["sym"] if str(e["sym"]).endswith("USDT") else f"{e['sym']}USDT"
        lo_ms = int(ts0 * 1000)
        hi_ms = int((ts0 + H) * 1000)
        try:
            cur.execute(
                "SELECT bid_px::float b, ask_px::float a"
                "  FROM asterdex_book_ticker"
                " WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s"
                "   AND bid_px>0 AND ask_px>bid_px"
                " ORDER BY event_ts_ms",
                (vs, lo_ms, hi_ms))
            d = cur.fetchall()
        except Exception as ex:
            print(f"    查询失败 {vs}: {type(ex).__name__}: {ex}")
            n_noob += 1
            continue
        if len(d) < 5:
            n_noob += 1
            continue
        mid = np.array([0.5 * (x["b"] + x["a"]) for x in d])
        side = str(r0.get("side") or "").lower()
        checked += 1
        checked_syms[e["sym"]] += 1
        if side == "buy":
            # 入场是买 ⇒ 出库要卖 ⇒ mid 回到入场价即可不亏
            hit = mid >= px
            hitp = mid >= px * (1 + 0.5 / 1e4)
        else:
            hit = mid <= px
            hitp = mid <= px * (1 - 0.5 / 1e4)
        if hit.any():
            reach += 1
            reach_ticks.append(int(hit.sum()))
        else:
            never += 1
        if hitp.any():
            reach_plus += 1

    cn.close()
    print(f"\n  ── 诊断 ──  无入场记录 {n_norec}  价格无效 {n_badpx}  "
          f"窗口内盘口不足 {n_noob}  ⇒ **实际检查 {checked}**")
    if checked == 0:
        return 0
    print(f"  检查的币种分布：{dict(checked_syms.most_common(8))}")

    print(f"\n  ── 结果（观察窗 {H}s，真实 mid 路径）──")
    print(f"    mid 曾回到「入场价」（可不亏离场）：**{reach}/{checked} = "
          f"{reach/checked*100:.1f}%**")
    print(f"    mid 曾到「入场价 + 0.5bp」（小赚）：{reach_plus}/{checked} = "
          f"{reach_plus/checked*100:.1f}%")
    print(f"    mid **从未**回到入场价：**{never}/{checked} = "
          f"{never/checked*100:.1f}%**")
    if reach_ticks:
        rt = np.array(reach_ticks)
        print(f"    回到入场价的 tick 数：中位 {np.median(rt):.0f}")

    print("\n" + "=" * 96)
    print("判据（事先定死）")
    print("=" * 96)
    r = reach / checked
    if r > 0.8:
        print(f"\n  ⇒ **{r*100:.0f}% 的强平仓位「本可离场」**（mid 曾回到入场价）")
        print(f"     ⇒ 是**我们没挂住/没等到**，不是物理上不可能")
        print(f"     ⇒ **第三种出库机制有空间**：更耐心地挂、或挂得更靠内")
    elif r > 0.5:
        print(f"\n  ⇒ {r*100:.0f}% 本可离场 ⇒ 部分有空间")
    else:
        print(f"\n  ⇒ 仅 {r*100:.0f}% 曾回到入场价")
        print(f"     ⇒ **多数在物理上无法不亏离场** ⇒ 强平必然 ⇒ 地板成立")
        print(f"     ⇒ 第三条路关闭；只能靠换场地降 taker")
    print(f"\n  ⚠️ mid 回到入场价 ≠ 我们的卖单一定成交（还需主动买打到且排在队首）")
    print(f"     ⇒ 这是**上界**，不是保证。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
