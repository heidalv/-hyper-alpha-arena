# -*- coding: utf-8 -*-
"""把排队来回拆成「前面还有多少钱、价差多宽」两档，写成每一拍要查的表。

某一档不够 30 笔，或买回来不赚，这一拍就跳过。不把整个币关掉。
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.services.market_maker.attribution import _market_dsn  # noqa: E402
from backend.services.market_maker.flow_rules import (  # noqa: E402
    AHEAD_EDGES_USD, MIN_N_EFF, SPREAD_EDGES_BP, situation_band,
)
import psycopg  # noqa: E402

# ── [整顿轮·T19 2026-10-05] 回看窗口必须可配，且不能跨"策略变更"混采 ──
#
# 事故（实测）：本脚本原先硬编码回看 **12 小时**。
# 而 2026-10-05 当天策略有 4 次**根本性改动**：
#   T2 15:21:24 吃单进场 → 成本门槛
#   T1 15:57:56 吃单止损 → 挂单抢平（这一步把 91% 的亏损源删掉）
#   T9 16:01:37 离场容差按波动缩放
#   T17 19:27:22 `maker_risk` → 被动侧
#
# 20:51 实测：12 小时窗内 **pre-fix 476 腿 / post-fix 116 腿 = 80% 是旧策略**。
# ⇒ 用一份"80% 由旧策略产生的往返样本"去训练门 ⇒
#   **门学的是已经被我们删掉的那个策略**，必然给出偏悲观的 mean_y。
#   实测该表 451 个档的 mean_y 中位数 = **−16.7bp**（而当前实盘是 +1.19/万名义）。
#
# 修法：`MM_SIT_LOOKBACK_HOURS` 可配（默认 12 保持历史行为）。
# 策略有重大变更后，应临时调小到"变更之后"的时长，避免新旧混采。
# 回滚：删掉 env 行即回到 12 小时。
try:
    LOOKBACK_HOURS = float(os.getenv("MM_SIT_LOOKBACK_HOURS", "12") or 12)
except ValueError:
    LOOKBACK_HOURS = 12.0
LOOKBACK_HOURS = max(0.25, min(48.0, LOOKBACK_HOURS))

_spec = importlib.util.spec_from_file_location(
    "h829_queue_split", ROOT / "scripts" / "h829_queue_split.py")
h829 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h829)


def bare(symbol: str) -> str:
    name = str(symbol or "").upper()
    return name[:-4] if name.endswith("USDT") else name


def listed(conn, lo_ms):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT b.symbol FROM ("
            "  SELECT symbol, count(*) AS c FROM asterdex_book_ticker"
            "  WHERE event_ts_ms>%s GROUP BY symbol) b "
            "JOIN ("
            "  SELECT symbol, count(*) AS c FROM asterdex_trades"
            "  WHERE event_ts_ms>%s GROUP BY symbol) t "
            "ON b.symbol=t.symbol "
            "WHERE b.c>=500 AND t.c>=200 ORDER BY b.c DESC",
            (lo_ms, lo_ms),
        )
        return [r[0] for r in cur.fetchall()]


def buckets(rows):
    groups = {}
    for row in rows:
        y = float(row[2])
        if y != y:
            continue
        key = (situation_band(row[4], AHEAD_EDGES_USD),
               situation_band(row[5], SPREAD_EDGES_BP))
        groups.setdefault(key, []).append(row)
    out = []
    for (ahead_i, spread_i), part in sorted(groups.items()):
        ys = np.array([p[2] for p in part], dtype=float)
        times = np.array([p[0] for p in part])
        cut = np.quantile(times, 0.66) if len(part) >= 3 else times[-1]
        recent = ys[times > cut]
        if len(recent) == 0:
            recent = ys
        out.append({
            "ahead_i": int(ahead_i),
            "spread_i": int(spread_i),
            "n": int(len(ys)),
            "mean_y": round(float(ys.mean()), 4),
            "median_y": round(float(np.median(ys)), 4),
            "recent_mean_y": round(float(recent.mean()), 4),
        })
    return out


def main():
    lo = int((time.time() - LOOKBACK_HOURS * 3600) * 1000)
    print(f"回看窗口 = {LOOKBACK_HOURS:.2f} 小时"
          f"（MM_SIT_LOOKBACK_HOURS；策略重大变更后应调小以避免新旧混采）",
          flush=True)
    coins = {}
    opened = []
    with psycopg.connect(_market_dsn(), autocommit=True) as conn:
        names = listed(conn, lo)
        print(f"有盘口也有成交的合约 {len(names)} 个", flush=True)
        for name in names:
            pack = h829.load(conn, name, lo)
            key = bare(name)
            if pack is None or len(pack[0]) < 500 or len(pack[5]) < 200:
                coins[key] = {"buy": [], "sell": []}
                print(f"  {key:<12} 数据不足", flush=True)
                continue
            coin = {}
            for side in ("buy", "sell"):
                _attempts, rows = h829.simulate(pack, side)
                coin[side] = buckets(rows)
            coins[key] = coin
            good = []
            for side in ("buy", "sell"):
                for row in coin[side]:
                    # [整顿轮·T19] 判据里的样本门槛必须与
                    # `flow_rules.MIN_N_EFF` **同源**，否则改了一个另一个不生效
                    # （生产 0/消费 30 不一致 ⇒ 修了等于没修）。
                    if (row["n"] >= MIN_N_EFF and row["mean_y"] > 1
                            and row["median_y"] > 0 and row["recent_mean_y"] > 0):
                        good.append(f"{side} 前面第{row['ahead_i']+1}档"
                                    f"价差第{row['spread_i']+1}档 {row['mean_y']:+.2f}")
            if good:
                opened.append(key)
            print(f"  {key:<12} {'可做 ' + '；'.join(good) if good else '这一拍都先跳过'}",
                  flush=True)
    doc = {
        "ts": time.time(),
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "hours": LOOKBACK_HOURS,
        "protocol": "tick_situation",
        "ahead_edges_usd": list(AHEAD_EDGES_USD),
        "spread_edges_bp": list(SPREAD_EDGES_BP),
        "coins": coins,
    }
    (ROOT / "data" / "flow_situation_last.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"写好 {len(coins)} 个币，眼前能做的 {len(opened)} 个：{', '.join(opened) or '无'}",
          flush=True)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
