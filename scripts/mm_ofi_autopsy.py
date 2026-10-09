# -*- coding: utf-8 -*-
"""[F229] OFI 接刀判据在 22:30~22:47 阴跌段为什么没拦住 57 连买 —— 用原始桶数据回答：
  ① 该窗口每 15s 桶的 OFI 分布（|OFI|>0.5 的比例 = 闸门最多能拦多少次）；
  ② OFI 与**下一桶**中价收益的相关性（Lu-Abergel 延续性在本次阴跌里是否成立）；
  ③ 如果换阈值 0.2/0.3、或看 3 桶累计 OFI，能拦住多少连买。
只读。
"""
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")

from sqlalchemy import text  # noqa: E402
from backend.database.connection import MarketSessionLocal  # noqa: E402

SINCE, UNTIL = "2026-09-15T22:24:00+08:00", "2026-09-15T22:50:00+08:00"


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


with MarketSessionLocal() as db:
    for sym in ("SOL", "BNB"):
        rows = db.execute(text(
            "SELECT timestamp, low_price, high_price,"
            " taker_buy_volume bv, taker_sell_volume sv"
            " FROM market_trades_aggregated"
            " WHERE exchange='asterdex' AND symbol=:s"
            " AND timestamp >= :a AND timestamp < :b"
            " ORDER BY timestamp ASC"
        ), {"s": sym, "a": _ms(SINCE), "b": _ms(UNTIL)}).mappings().all()
        buckets = []
        for r in rows:
            bv, sv = float(r["bv"] or 0), float(r["sv"] or 0)
            mid = (float(r["low_price"] or 0) + float(r["high_price"] or 0)) / 2
            ofi = (bv - sv) / (bv + sv) if (bv + sv) > 0 else 0.0
            buckets.append({"ofi": ofi, "mid": mid, "tot": bv + sv})
        if len(buckets) < 3:
            print(f"{sym}: 桶太少 {len(buckets)}")
            continue
        ofis = np.array([b["ofi"] for b in buckets])
        mids = np.array([b["mid"] for b in buckets if b["mid"] > 0])
        nxt = []
        for i in range(len(buckets) - 1):
            m0, m1 = buckets[i]["mid"], buckets[i + 1]["mid"]
            nxt.append((m1 / m0 - 1) * 1e4 if m0 > 0 else 0.0)
        nxt = np.array(nxt)
        corr = np.corrcoef(ofis[:-1], nxt)[0, 1] if len(nxt) > 2 else float("nan")
        n_gt = {t: int((np.abs(ofis) > t).sum()) for t in (0.2, 0.3, 0.5)}
        n_neg = int((ofis < 0).sum())
        # 3 桶累计（滑动和）
        cum3 = np.array([ofis[max(0, i - 2):i + 1].sum() for i in range(len(ofis))])
        n_cum = {t: int((np.abs(cum3) > t).sum()) for t in (0.5, 1.0, 1.5)}
        print(f"== {sym} == 桶数={len(buckets)}")
        print(f"   OFI 分布: mean={ofis.mean():+.3f}  min={ofis.min():+.3f} max={ofis.max():+.3f}")
        print(f"   负 OFI(卖压) 桶占比: {n_neg}/{len(buckets)} = {n_neg/len(buckets):.0%}")
        print(f"   |OFI|>阈值 的桶数: 0.2→{n_gt[0.2]}  0.3→{n_gt[0.3]}  0.5→{n_gt[0.5]}")
        print(f"   |3桶累计OFI|>阈值: 0.5→{n_cum[0.5]}  1.0→{n_cum[1.0]}  1.5→{n_cum[1.5]}")
        print(f"   corr(本桶OFI, 下一桶中价收益) = {corr:+.3f}"
              f"  （>0 = 延续性成立，OFI 判据有效）")
        print(f"   中价收益桶均 = {nxt.mean():+.3f}bp / 桶，本窗口累计 = {nxt.sum():+.1f}bp")
