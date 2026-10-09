# -*- coding: utf-8 -*-
"""[H159 2026-09-21] 超时不出库的持仓到底卡多久 —— 量化 `timeout_exit_maker_only` 的代价。

# 用户反馈

面板上看到 `XRP 多 520.7162 · 开仓 1.4297 · 现价 1.4295 · 持有 6m26s`。
而 `max_one_side_seconds = 300`（5 分钟）⇒ **超过上限仍在持仓**。

这是 `timeout_exit_maker_only=True` 的**预期行为**（超时不再 taker，改等减仓侧挂单被吃），
但必须量化它到底把持仓拖到多久、以及这段时间换来的是省钱还是更多风险。

# 量什么

  · 每个持仓的"年龄"分布（`opened_ts` → now）
  · 有多少个超过 300s 仍未出库（= 超时被挡下的实际表现）
  · 这些超龄持仓的浮亏分布（等被动出库是否在赔行情）
  · `timeout_exit_blocked` 计数 vs 实际仍然开着的仓位（是否一一对应）

# 判据（事先定死）

  · 若超龄持仓的**浮亏普遍很小**（< 5bp）⇒ 等被动是划算的（省 4bp taker > 赔的行情）
  · 若超龄持仓**浮亏持续扩大**（> 20bp）⇒ 说明"价格不回来"，该恢复 taker 或收紧
    减仓侧挂宽（`spread_mult_reduce` 现在还是 0.95，偏宽，等它成交要更久）
  · 若大量仓位年龄远超 300s（如 > 20 分钟）⇒ 被动出库路径**实际上失效**，
    必须查减仓侧报价是否根本没挂出去

用法：
    .venv\\Scripts\\python.exe scripts\\h159_stuck_positions.py
"""
from __future__ import annotations

import json
import statistics as st
import time
from datetime import datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("DATABASE_URL", "")
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def live_mids(symbols) -> dict:
    """从最新盘口取 mid（用于算浮盈）。"""
    if not symbols:
        return {}
    murl = dsn().rsplit("/", 1)[0] + "/alpha_market"
    out = {}
    with psycopg.connect(murl) as c:
        with c.cursor() as cur:
            for s in symbols:
                cur.execute("""
                    SELECT (bid_px+ask_px)/2 FROM asterdex_book_ticker
                    WHERE symbol=%s ORDER BY event_ts_ms DESC LIMIT 1
                """, (s + "USDT",))
                r = cur.fetchone()
                if r and r[0]:
                    out[s] = float(r[0])
    return out


def main() -> int:
    st_file = ROOT / "logs" / "mm_lane_status.json"
    j = json.loads(st_file.read_text(encoding="utf-8"))
    age_file = int((datetime.now()
                    - datetime.fromtimestamp(st_file.stat().st_mtime)).total_seconds())
    limits = j.get("limits") or {}
    max_hold = float(limits.get("max_one_side_seconds") or 0.0)
    now = time.time()

    states = j.get("states") or {}
    open_pos = {s: v for s, v in states.items()
                if abs(float(v.get("qty") or 0.0)) > 1e-12}
    mids = live_mids(list(open_pos))

    print("=" * 96)
    print("H159  超时不出库的持仓：卡多久、代价多大")
    print("=" * 96)
    print(f"  心跳年龄 {age_file}s   `max_one_side_seconds` = {max_hold:.0f}s")
    print(f"  `timeout_exit_maker_only` = {limits.get('timeout_exit_maker_only')}")
    print(f"  `spread_mult_reduce`（减仓侧挂宽）= "
          f"{(j.get('params') or {}).get('spread_mult_reduce')}")
    print(f"  `timeout_exit_blocked` 累计 = {j.get('timeout_exit_blocked')}")

    if not open_pos:
        print("\n  当前无持仓。")
        return 0

    print(f"\n  {'币':<8} {'方向':<5} {'qty':>12} {'开仓mid':>10} {'现价':>10} "
          f"{'持有s':>8} {'超龄':>6} {'浮盈$':>9} {'浮盈bp':>8}")
    print("  " + "-" * 92)
    ages = []
    upls = []
    for s, v in sorted(open_pos.items()):
        qty = float(v.get("qty") or 0.0)
        avg_mid = float(v.get("avg_mid") or 0.0)
        op = float(v.get("opened_ts") or 0.0)
        age = (now - op) if op > 0 else 0.0
        mid = float(mids.get(s) or 0.0)
        upl = qty * (mid - avg_mid) if (mid and avg_mid) else None
        # ⚠️ 符号：`upl` 已经是「带方向的盈亏」（空头价格涨 ⇒ 负）。
        # 而 `(mid-avg_mid)/avg_mid` 是**价格变动**，不是盈亏 —— 对空头方向相反。
        # 第一版直接拿它当"浮盈bp"，导致空头浮亏却显示 +4bp（自相矛盾）。
        # 正确做法：用**盈亏方向**折算 = sign(qty) × 价格变动。
        bp = (upl / (abs(qty) * avg_mid) * 1e4) if (upl is not None and qty and avg_mid) else None
        ages.append(age)
        if upl is not None:
            upls.append(upl)
        over = "**是**" if (max_hold > 0 and age > max_hold) else ""
        print(f"  {s:<8} {'多' if qty > 0 else '空':<5} {qty:>12.4f} {avg_mid:>10.4f} "
              f"{mid:>10.4f} {age:>8.0f} {over:>6} "
              f"{(f'{upl:+.4f}' if upl is not None else '—'):>9} "
              f"{(f'{bp:+.2f}' if bp is not None else '—'):>8}")

    over = [a for a in ages if max_hold > 0 and a > max_hold]
    print(f"\n  ── 统计 ──")
    print(f"    持仓 {len(ages)} 个   持有中位 {st.median(ages):.0f}s   "
          f"最大 {max(ages):.0f}s")
    print(f"    超过 {max_hold:.0f}s 上限的 **{len(over)}** 个"
          f"（占 {len(over)/len(ages)*100:.0f}%）")
    if upls:
        print(f"    浮盈合计 **{sum(upls):+.4f} USD**   "
              f"中位 {st.median(upls):+.4f}   最差 {min(upls):+.4f}")

    print(f"\n  ── 判据核对 ──")
    if over and upls:
        worst = min(upls)
        print(f"    超龄持仓的最差浮亏 {worst:+.4f} USD")
        print(f"    参考：一次 taker 平仓的成本 = 4bp × 单腿名义 "
              f"= 4bp × {(j.get('fill_notional') or 0):,.0f} = "
              f"{(j.get('fill_notional') or 0)*0.0004:.3f} USD")
        print(f"    ⇒ 若浮亏 < 那次 taker 成本 ⇒ 等被动是划算的；反之则不该等")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
