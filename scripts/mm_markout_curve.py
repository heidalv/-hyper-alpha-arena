"""逐笔 markout 曲线：被动做市到底在"多长的视野上"被打（毒流诊断的行业标准口径）。

[F198 2026-09-15] 为什么这是决定性的一步
--------------------------------------------------
已确认的两件事把问题夹到了一个很窄的区间：
  · 账本价格项 **−441.84$**（亏损几乎全在这里）✗；
  · 但按**持仓路径盯市到 15 分钟**只有 **+10.72$** ✓ —— 说明：
      **持仓方向在 15 分钟尺度上基本无偏（顺向占比 44~52%，方向期望 bp 都在 ±2bp 内）** ✗
      ⇒ "跟着趋势站错边"这个解释**不成立** ✗；
  ⇒ 那么"价格项"的 −442$ 只能是在**更短**的时间里实现的 ✓✓
      （账本 price_bp 记在减仓那一笔上，而做市持仓中位数只有 ~30s ✓）。

本脚本直接量这条曲线：对每一笔成交，取该笔的**成交价方向**，
计算之后 15s/30s/60s/120s/300s/600s/1800s 的中价变动（**顺为负、逆为正**的"被打"口径）：
    markout(h) = 方向 × (mid_{t+h} − mid_t) / mid_t      （买=+1，卖=−1）
  · markout < 0 ⇒ 成交后价格继续朝不利方向走 ⇒ **被逆向选择（毒流）** ✗
  · markout 先负后正 ⇒ **短期被打、长期回复** ⇒ 正确对策是"**别在最差时刻平仓**"（持有到回复 ✓）
    而不是"预测趋势" ✓✓ —— 这两者的工程含义完全不同（前者改**出场逻辑**，后者要外部信号）

还会按 **买入腿 / 卖出腿**、**时代（配置变更前后）** 分别给出，便于定位是哪一段变坏。
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
from sqlalchemy import text  # noqa: E402

from backend.database.connection import MarketSessionLocal, SessionLocal  # noqa: E402

LANE = os.getenv("MM_LANE", "mm_asterdex")
SYMS = ["BTC", "ETH", "BNB", "XRP", "SOL"]
TZ = timezone(timedelta(hours=8))
HORIZONS = [15, 30, 60, 120, 300, 600, 1800]


def _rows(sql: str, **p) -> List[dict]:
    with SessionLocal() as s:
        s.execute(text("SET statement_timeout = 60000"))
        return [dict(r) for r in s.execute(text(sql), p).mappings().all()]


def _dt(v) -> datetime:
    d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
    return d.replace(tzinfo=TZ) if d.tzinfo is None else d


def main() -> int:
    since = sys.argv[1] if len(sys.argv) > 1 else "2026-09-14T11:50:00+08:00"
    since_dt = datetime.fromisoformat(since)
    fills = _rows("""SELECT ts, symbol, notional, net_bp, meta_json FROM lane_ledger
                     WHERE lane_id=:l AND event='fill' AND ts >= :a ORDER BY ts""",
                  l=LANE, a=since_dt)
    print(f"逐笔 markout 曲线 · {LANE} · 起点 {since} · 成交 {len(fills)} 笔")
    if not fills:
        return 1

    t0 = min(_dt(f["ts"]) for f in fills).timestamp()
    t1 = max(_dt(f["ts"]) for f in fills).timestamp()
    lo, hi = int((t0 - 120) * 1000), int((t1 + max(HORIZONS) + 120) * 1000)
    mids: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
    for s in SYMS:
        ts_l, m_l = [], []
        with MarketSessionLocal() as ses:
            ses.execute(text("SET statement_timeout = 60000"))
            for r in ses.execute(text(
                    """SELECT timestamp, best_bid, best_ask FROM market_orderbook_snapshots
                       WHERE symbol=:s AND timestamp BETWEEN :a AND :b ORDER BY timestamp"""),
                    {"s": s, "a": lo, "b": hi}).mappings().all():
                if r["best_bid"] and r["best_ask"]:
                    ts_l.append(int(r["timestamp"]) / 1000.0)
                    m_l.append((float(r["best_bid"]) + float(r["best_ask"])) / 2)
        mids[s] = (np.array(ts_l), np.array(m_l))
        print(f"  行情 {s}: {len(ts_l)} 快照")

    def markouts(side: str, rows: List[dict]) -> Dict[int, List[float]]:
        out: Dict[int, List[float]] = defaultdict(list)
        for f in rows:
            s = f["symbol"]
            ts_arr, mid_arr = mids.get(s) or (np.array([]), np.array([]))
            if len(ts_arr) < 10:
                continue
            t = _dt(f["ts"]).timestamp()
            j = int(np.searchsorted(ts_arr, t, "right")) - 1
            if j < 0:
                continue
            m0 = float(mid_arr[j])
            if m0 <= 0:
                continue
            sign = 1.0 if side == "buy" else -1.0
            for h in HORIZONS:
                k = int(np.searchsorted(ts_arr, t + h, "right")) - 1
                if k > j:
                    out[h].append(sign * (float(mid_arr[k]) - m0) / m0 * 1e4)
        return out

    def markouts_one_side(rows: List[dict]) -> Dict[int, List[float]]:
        """按**每一笔自己的方向**算 markout（不能用同一批行套两种符号 ✗）。

        第一版就在这里错了：合并行写成 `markouts("buy", rows)` 与 `markouts("sell", rows)`
        再平均 ⇒ 同一批成交被套上 +1 与 −1 两个符号 ⇒ **恒等于 0** ✗✗
        （打出"全 0.00"的假结论，正好把真实的卖腿毒流掩盖掉）。
        正确口径：买 = +Δmid，卖 = −Δmid，逐笔按 meta 里的 side 取符号 ✓。
        """
        out: Dict[int, List[float]] = defaultdict(list)
        for f in rows:
            s = f["symbol"]
            ts_arr, mid_arr = mids.get(s) or (np.array([]), np.array([]))
            if len(ts_arr) < 10:
                continue
            side = str((f.get("meta_json") or {}).get("side") or "").lower()
            if not (side.startswith("b") or side.startswith("s")):
                continue
            t = _dt(f["ts"]).timestamp()
            j = int(np.searchsorted(ts_arr, t, "right")) - 1
            if j < 0:
                continue
            m0 = float(mid_arr[j])
            if m0 <= 0:
                continue
            sign = 1.0 if side.startswith("b") else -1.0
            for h in HORIZONS:
                k = int(np.searchsorted(ts_arr, t + h, "right")) - 1
                if k > j:
                    out[h].append(sign * (float(mid_arr[k]) - m0) / m0 * 1e4)
        return out

    def show(title: str, rows: List[dict]) -> None:
        if not rows:
            print(f"\n{title}: 无样本")
            return
        print(f"\n{title}（成交 {len(rows)} 笔，名义 "
              f"${sum(float(f['notional'] or 0) for f in rows):,.0f}）")
        print(f"  {'方向':<6}" + "".join(f"{str(h)+'s':>9}" for h in HORIZONS))
        for side in ("buy", "sell"):
            sub = [f for f in rows if str((f.get("meta_json") or {}).get("side") or "").lower().startswith(side[0])]
            mo = markouts(side, sub)
            if not mo:
                continue
            line = f"  {side:<6}"
            for h in HORIZONS:
                v = mo.get(h) or []
                line += f"{(np.mean(v) if v else float('nan')):>+9.2f}"
            print(line + f"   n={len(sub)}")
        # 正确合并：逐笔按自己的方向取符号（见 markouts_one_side 的注释 ✗→✓）
        mo_all = markouts_one_side(rows)
        line = f"  {'合并':<6}"
        for h in HORIZONS:
            v = mo_all.get(h) or []
            line += f"{(np.mean(v) if v else float('nan')):>+9.2f}"
        print(line + f"   n={len(rows)}")

    show("【全时代】", fills)

    # 时代切分：F189 部署（12:57）与数据修复（08:00）
    for label, a, b in (
        ("数据修复前(08:00 之前)", "2026-09-14T11:50:00+08:00", "2026-09-15T08:00:00+08:00"),
        ("修复后/变更前(08:00~12:57)", "2026-09-15T08:00:00+08:00", "2026-09-15T12:57:00+08:00"),
        ("F189 变更中(12:57~14:02)", "2026-09-15T12:57:00+08:00", "2026-09-15T14:02:00+08:00"),
    ):
        sub = [f for f in fills if a <= _dt(f["ts"]).astimezone(TZ).isoformat() < b]
        show(f"【{label}】", sub)

    print("\n读法：markout 为负 = 成交后价格继续朝不利方向走（被逆向选择/毒流）✗")
    print("  · 只在最前面几档为负、后面转正 ⇒ 「被扫到后短期受损、随后回复」")
    print("    ⇒ 对策是**改出场逻辑（别在最差时刻平仓）**，不需要外部方向信号 ✓")
    print("  · 全程为负 ⇒ 真正的知情流 ⇒ 需要外部方向资源做**准入/敞口**闸门 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
