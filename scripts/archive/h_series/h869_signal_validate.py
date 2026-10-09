# -*- coding: utf-8 -*-
"""[h869] 专属信号离线验证:**大单冲击 × 深度真空** vs 通用 OFI。

问题:我们的入场为什么亏?h828 实测:被动挂单成交后的 markout 全为负
(30s −1.6 / 180s −13.1 / 300s −10.7bp)⇒ 通用 OFI 在被我们成交的价格上没有预测力。

假设(专属信号):能不能推动价格,不只看**量差**,还要看**对侧深度厚不厚**。
  · 方向 := 近 5s 大单失衡(单笔 ≥3× 近 30 分钟中位数的主动成交,按方向净额)
  · 可行 := 同侧 10bp 内可见挂单量 / 近 30s 成交额(越小 = 越薄 = 容易推动)
  · 信号 := 失衡绝对值 > θ 且 对侧深度比 < 阈值 ⇒ 吃单方向性进场

本脚本:用历史逐笔 + 盘口数据,比较三者的**事后 markout**:
  A. 通用 OFI(5s 买卖量差)
  B. 大单失衡
  C. 大单失衡 × 深度真空(我们的专属信号)
markout 口径:信号时刻的中价 → +30s/+120s 中价,按信号方向取符号(正 = 方向对了)。
"""
import io
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
from backend.services.market_maker.attribution import _market_dsn  # noqa: E402
import psycopg  # noqa: E402

HOURS = 3                      # 回看小时数
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "NEARUSDT", "AAVEUSDT", "INJUSDT"]
STEP_S = 2.0                   # 每 2 秒出一个样本
WIN_S = 5.0                    # 信号窗口
MED_WIN_S = 1800.0             # 大单判定的中位数窗口
TH_IMB = 0.6                   # 失衡阈值(0~1)
TH_VAC = 0.20                  # 深度真空阈值(同侧 10bp 挂单 / 30s 成交额)
VAC_LOOK_S = 30.0

with psycopg.connect(_market_dsn(), autocommit=True) as c:
    cur = c.cursor()
    t0 = time.time() - HOURS * 3600
    rows_t = {}
    rows_b = {}
    for sym in SYMS:
        cur.execute(
            "SELECT event_ts_ms/1000.0, price, qty, is_buyer_maker"
            " FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > %s"
            " ORDER BY event_ts_ms",
            (sym, int(t0 * 1000)))
        rows_t[sym] = cur.fetchall()
        cur.execute(
            "SELECT event_ts_ms/1000.0, bid_px, ask_px, bid_qty, ask_qty"
            " FROM asterdex_book_ticker WHERE symbol=%s AND bid_px>0"
            " AND event_ts_ms > %s ORDER BY event_ts_ms",
            (sym, int(t0 * 1000)))
        rows_b[sym] = cur.fetchall()
        print(f"  {sym}: 逐笔 {len(rows_t[sym])} 条 / 盘口 {len(rows_b[sym])} 条",
              file=sys.stderr)

BIG = 3.0

def markouts_by_signal(sym, thr):
    """返回三类信号的 markout 列表(正 = 方向对了)。"""
    tr = rows_t[sym]
    bk = rows_b[sym]
    if len(tr) < 200 or len(bk) < 200:
        return [], [], []
    ts_arr = np.array([float(r[0]) for r in tr])
    price = np.array([float(r[1]) for r in tr])
    qty = np.array([float(r[2]) for r in tr])
    is_buy = np.array([not r[3] for r in tr])          # is_buyer_maker=False ⇒ 主动买
    bts = np.array([float(r[0]) for r in bk])
    bbid = np.array([float(r[1]) for r in bk])
    bask = np.array([float(r[2]) for r in bk])
    bq = np.array([(float(r[3]) + float(r[4])) for r in bk])  # 盘口两侧总量(近似)

    out_ofi, out_imb, out_vac = [], [], []
    t_end = min(ts_arr[-1], bts[-1]) - 120
    t = max(ts_arr[0], bts[0]) + MED_WIN_S
    while t < t_end:
        # 中位数(近 MED_WIN_S)
        m = np.median(qty[(ts_arr > t - MED_WIN_S) & (ts_arr <= t)])
        # 近 WIN_S 窗口
        mask = (ts_arr > t - WIN_S) & (ts_arr <= t)
        if not mask.any():
            t += STEP_S
            continue
        vol = float(qty[mask].sum())
        big_full = qty >= BIG * max(float(m), 1e-9)
        buy_vol = float(qty[mask & is_buy].sum())
        sell_vol = float(qty[mask & (~is_buy)].sum())
        ofi = (buy_vol - sell_vol) / max(vol, 1e-9)
        imb = (float(qty[mask & is_buy & big_full].sum())
               - float(qty[mask & (~is_buy) & big_full].sum())) / max(vol, 1e-9)
        # 当前中价 + 深度真空:进攻方向要打穿的"近侧盘口量" / 近 30s 成交额
        bi = int(np.searchsorted(bts, t, side="right") - 1)
        if bi < 0:
            t += STEP_S
            continue
        mid = (bbid[bi] + bask[bi]) / 2.0
        if mid <= 0:
            t += STEP_S
            continue
        mask30 = (ts_arr > t - VAC_LOOK_S) & (ts_arr <= t)
        not30 = float((price[mask30] * qty[mask30]).sum())
        if not30 <= 0:
            t += STEP_S
            continue
        near_qty = float(bq[bi] / 2.0)          # 近侧 ≈ 一侧挂单量(顶档)
        vac = (near_qty * mid) / not30
        # markout:+30s / +120s 中价,按方向取符号
        def mo(hor):
            j = int(np.searchsorted(bts, t + hor, side="right") - 1)
            if j <= bi:
                return None
            return (bbid[j] + bask[j]) / 2.0 / mid - 1.0
        m30, m120 = mo(30), mo(120)
        if m30 is None or m120 is None:
            t += STEP_S
            continue
        m30, m120 = m30 * 1e4, m120 * 1e4
        if abs(ofi) >= 0.2:
            s = 1.0 if ofi > 0 else -1.0
            out_ofi.append((s * m30, s * m120))
        if abs(imb) >= TH_IMB:
            s = 1.0 if imb > 0 else -1.0
            out_imb.append((s * m30, s * m120))
        if abs(imb) >= TH_IMB and vac < TH_VAC:
            s = 1.0 if imb > 0 else -1.0
            out_vac.append((s * m30, s * m120))
        t += STEP_S
    return out_ofi, out_imb, out_vac

print("== 信号 markout 对比(正 = 方向对;t = 均值/标准误) ==")
agg = {"OFI": [], "IMB": [], "VAC": []}
for sym in SYMS:
    a, b, c2 = markouts_by_signal(sym, TH_IMB)
    agg["OFI"] += a
    agg["IMB"] += b
    agg["VAC"] += c2
    print(f"  {sym:<10} OFI n={len(a):>4} | IMB n={len(b):>4} | VAC n={len(c2):>4}",
          file=sys.stderr)
for k, v in agg.items():
    if not v:
        print(f"  {k}: 无样本")
        continue
    m30 = np.array([x[0] for x in v])
    m120 = np.array([x[1] for x in v])
    t30 = m30.mean() / (m30.std(ddof=1) / np.sqrt(len(m30))) if len(m30) > 1 else 0
    t120 = m120.mean() / (m120.std(ddof=1) / np.sqrt(len(m120))) if len(m120) > 1 else 0
    print(f"  {k:<4} n={len(v):>4} | 30s markout {m30.mean():+7.2f}bp (t={t30:+5.2f})"
          f" | 120s {m120.mean():+7.2f}bp (t={t120:+5.2f})"
          f"  {'★正且显著' if m30.mean() > 0 and t30 > 2 else ''}")
