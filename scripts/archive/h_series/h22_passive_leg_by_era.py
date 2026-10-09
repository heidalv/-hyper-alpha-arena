"""H22：把被动腿亏损按**时代**拆开 —— 900s 改动前后到底变了什么？

## 为什么必须拆（H21 暴露的矛盾）

H21 实测（6h 窗口，账户账本）：
    phase=fill     219 笔   −$0.6865   （≈ −$0.0031/笔，胜率 75.8%）
    phase=flatten    4 笔   −$0.1391   （≈ −$0.0348/笔）

而更早的读数（3h 窗口）是 `fill 98 笔 −$0.0708`（≈ −$0.0007/笔）。
**被动腿的单笔亏损翻了 ~4 倍。** 两种可能，含义完全相反：

  (A) `max_one_side_seconds` 300→900 **导致**的：持仓时间从几分钟拉长到
      20~40 分钟 ⇒ 暴露在更多中价漂移上 ⇒ 被动腿亏得更多。
      若成立，放宽超时是**用强平费换持仓风险**，未必是改进。
  (B) **时代混淆**：早期那 98 笔里有相当一部分属于 `counter_trend` 旧配置期
      （结构性封锁大量成交、样本不同）。两段不可比。

## 判定方法

按时间把被动腿分桶，看**每笔均亏损随时代**的走势，并给出每个桶里
`max_one_side_seconds` 的实际取值（从 `lane_registry` 的变更时间推不出来，
所以改用**配置变更的时间点**作为分界，并把两个分界都试一遍）。

**不许**跨时代求和 —— 本项目已多次因跨窗口/跨时代求和得出错误结论。

用法：
    .venv\\Scripts\\python.exe scripts\\h22_passive_leg_by_era.py --hours 8
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

# 已确认的参数变更时刻（本地时间 +08:00）。这些是**事实**，不是推测：
#   18:43:50  worker 重启并采用 H16 参数（w_base 30→1.5、side_mode→both）
#   19:20      H17 超时 300s → 900s 写入注册表，runner 60s 内热采用
ERA_BOUNDARIES = [
    ("H16 前（w=30bp, counter_trend）", None, "2026-09-20T18:43:50+08:00"),
    ("H16 后 / 900s 前（w=1.5bp, both, 300s）",
     "2026-09-20T18:43:50+08:00", "2026-09-20T19:21:00+08:00"),
    ("900s 后（w=1.5bp, both, 900s）",
     "2026-09-20T19:21:00+08:00", None),
]


def _epoch(iso):
    """ISO 字符串 → epoch 秒（float）。None → None。

    ⚠️ 必须用 epoch 比较，不能直接比较 datetime：
       `arbitrage_paper_ledgers.created_at` 是 **naive** 时间戳（本地墙钟），
       而 `datetime.fromisoformat("...+08:00")` 是 **aware** ⇒ 直接比较会
       `TypeError: can't compare offset-naive and offset-aware datetimes`。
       （这是本项目第三次在时间戳时区上踩坑，统一走 epoch。）
    """
    if not iso:
        return None
    return datetime.fromisoformat(iso).timestamp()


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--account-id", type=int, default=101)
    args = ap.parse_args()

    import numpy as np
    import psycopg2
    import psycopg2.extras

    cn = psycopg2.connect(_dsn("alpha_arena"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT created_at, amount_usd, metadata_json
          FROM arbitrage_paper_ledgers
         WHERE account_id = %s AND action = 'paper_pnl'
           AND created_at > now() - make_interval(secs => %s)
         ORDER BY created_at
        """,
        (args.account_id, args.hours * 3600.0),
    )
    rows = cur.fetchall()

    legs = []
    for r in rows:
        try:
            md = json.loads(r["metadata_json"] or "{}")
        except Exception:
            md = {}
        t = r["created_at"]
        # naive 时间戳按**本地时区**解释（库列语义 = 本地墙钟，已多次确认）
        if t.tzinfo is None:
            t_ep = t.replace(tzinfo=datetime.now().astimezone().tzinfo).timestamp()
        else:
            t_ep = t.timestamp()
        legs.append({
            "ts": t, "ts_ep": t_ep,
            "amt": float(r["amount_usd"] or 0.0),
            "phase": str(md.get("phase") or "?"),
            "symbol": md.get("symbol") or "-",
        })
    print(f"H22 被动腿按时代拆分  账户={args.account_id}  窗口={args.hours}h  共 {len(legs)} 笔\n")
    if not legs:
        return 1

    print("[1] 各时代的被动腿（phase=fill）")
    print("    %-42s %6s %13s %13s %8s" % ("时代", "笔数", "净额$", "每笔均$", "胜率"))
    for label, lo, hi in ERA_BOUNDARIES:
        lo_ep, hi_ep = _epoch(lo), _epoch(hi)
        sel = [x for x in legs if x["phase"] == "fill"
               and (lo_ep is None or x["ts_ep"] >= lo_ep)
               and (hi_ep is None or x["ts_ep"] < hi_ep)]
        if not sel:
            print("    %-42s %6d %13s %13s %8s" % (label, 0, "—", "—", "—"))
            continue
        usd = sum(x["amt"] for x in sel)
        wins = sum(1 for x in sel if x["amt"] > 0)
        print("    %-42s %6d %+13.6f %+13.6f %7.1f%%"
              % (label, len(sel), usd, usd / len(sel), 100.0 * wins / len(sel)))

    print("\n[2] 每 20 分钟一桶（看趋势，不跨桶求和）")
    print("    %-20s %8s %6s %13s %13s %8s"
          % ("时段", "fill数", "占比", "净额$", "每笔均$", "胜率"))
    fills = [x for x in legs if x["phase"] == "fill"]
    if fills:
        buckets = defaultdict(list)
        for x in fills:
            t = x["ts"]
            key = t.replace(minute=(t.minute // 20) * 20, second=0, microsecond=0)
            buckets[key].append(x)
        for k in sorted(buckets):
            sel = buckets[k]
            usd = sum(x["amt"] for x in sel)
            wins = sum(1 for x in sel if x["amt"] > 0)
            print("    %-20s %8d %6s %+13.6f %+13.6f %7.1f%%"
                  % (k.strftime("%H:%M"), len(sel), "", usd, usd / len(sel),
                     100.0 * wins / len(sel)))

    print("\n[3] 判定")
    era = {}
    for label, lo, hi in ERA_BOUNDARIES:
        lo_ep, hi_ep = _epoch(lo), _epoch(hi)
        sel = [x for x in legs if x["phase"] == "fill"
               and (lo_ep is None or x["ts_ep"] >= lo_ep)
               and (hi_ep is None or x["ts_ep"] < hi_ep)]
        if sel:
            era[label] = (len(sel), sum(x["amt"] for x in sel) / len(sel))
    if len(era) >= 2:
        keys = list(era.keys())
        print("    各时代每笔均亏损：")
        for k in keys:
            print("      %-42s n=%4d  %+.6f$/笔" % (k, era[k][0], era[k][1]))
        a, b = era[keys[-1]][1], era[keys[0]][1]
        print("\n    最早 vs 最新时代 每笔均差 = %+.6f$" % (a - b))
        if a < b * 0.6:
            print("    ⇒ 最新时代**明显更差**（每笔均亏损至少恶化 40%）")
            print("      结合 H21「被动腿 −$0.6865/219 笔」，倾向解释 (A)：")
            print("      放宽超时把成本从『强平费』转移到了『持仓漂移』，未必是净改进。")
            print("      ⚠️ 但这**不是因果证明**：时代之间市场波动（avg_sigma_all 0.06→0.12）")
            print("         也在变，需要同窗口对照才能定因。")
        else:
            print("    ⇒ 时代之间差异不足以支持『放宽超时变差』的结论；")
            print("      H21 的 −$0.6865/219 更可能来自**早先读数窗口过窄**（样本少）。")
    else:
        print("    样本不足两个时代，无法比较")

    print("\n    ⚠️ 必读：本脚本只做**时代拆分**，不构成因果证据。")
    print("       时代之间同时变化的有：w_base(30→1.5)、side_mode(counter_trend→both)、")
    print("       max_one_side_seconds(300→900)、以及**市场本身**。")
    print("       要定因必须**同窗口回放**（改一个参数、跑同一段快照）。")
    cur.close()
    cn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
