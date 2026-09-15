"""行情自身的短程记忆（自相关/响应函数）——决定"被动做市该往哪个方向偏斜"。

[F187 2026-09-15] 动机
--------------------------------------------------
干净数据下已确认的事实（F181~F185）：
  · 每名义边际为负（全时代 −0.34bp；干净窗口实盘 −1.89bp / 模型 −1.07bp）✗
  · 分解恒为「价差 +4.3bp」抵消不掉「价格 −4.6bp」⇒ 亏损在**被动成交后的漂移** ✓
  · 越窄越亏（成交更多 ⇒ 负边际被周转放大）✓

被动挂单的盈亏 = 价差捕获 − 成交后漂移。策略里唯一直接对抗漂移的旋钮是**库存偏斜**
（`k_inv`：持仓越多，减仓侧挂得越窄）。它隐含一个假设：**价格会回归**（成交后往回走）。
如果该场馆在我们成交的时间尺度上其实是**动量**的，那么：
  · 现有偏斜方向就是**错的符号**（越跌越买、越涨越卖 = 逆势 ✗）；
  · 正确做法是**顺着最近一段走势挂**（涨了就把卖单挂窄、买单挂远）✓。
本脚本不做任何策略假设，直接**测量**这个符号：中价的滞后相关与条件响应。

口径
----
· 中价 = (best_bid + best_ask)/2（快照网格 ~15s，与做市 tick 同量级）；
· 收益 r_i（bp）= 相邻快照中价变化；滞后 k 表示"k 个快照 ≈ 15k 秒"；
· 条件响应 = 条件在 |r_i| 处于尾部（前/后 10%）时，后续 k 期累计收益的均值；
· 全部只读行情库，不依赖任何成交判定 ⇒ 与 F124 的 markout 分析互相独立 ✓。
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from backend.services.market_maker.portfolio_replay import _load_all  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTC,ETH,BNB,XRP,SOL")
    ap.add_argument("--venue", default="asterdex")
    ap.add_argument("--hours", type=float, default=6.0, help="只取最近 N 小时")
    ap.add_argument("--lags", default="1,2,4,8,16")
    ap.add_argument("--tail-q", type=float, default=0.10, help="条件响应的尾部比例")
    args = ap.parse_args()

    syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    lags = [int(x) for x in args.lags.split(",") if x.strip()]
    data = _load_all(syms, args.venue)

    print(f"行情短程记忆 · {args.venue} · 最近 {args.hours}h · 快照中价 ~15s 网格")
    print(f"{'币':<5}{'样本':>7}{'σ(bp,1期)':>11}" +
          "".join(f"{'ρ' + str(k):>9}" for k in lags) +
          f"{'E[r+|涨尾]':>11}{'E[r+|跌尾]':>11}{'n+':>6}{'n-':>6}")
    all_r: List[np.ndarray] = []
    resp_up, resp_dn = [], []
    for s in syms:
        d = data[s]
        ots, bb, ba = d["ots"], d["bb"], d["ba"]
        if len(ots) < 200:
            print(f"{s:<5} 样本不足 ({len(ots)})")
            continue
        cut = ots[-1] - int(args.hours * 3.6e6)
        m = ots >= cut
        mid = (bb[m] + ba[m]) / 2.0
        bad = ~np.isfinite(mid) | (mid <= 0)
        mid = mid[~bad]
        if len(mid) < 200:
            print(f"{s:<5} 样本不足 ({len(mid)})")
            continue
        r = np.diff(mid) / mid[:-1] * 1e4
        r = np.clip(r, -200.0, 200.0)   # 去掉明显的脏点
        all_r.append(r)
        sd = float(np.std(r))
        line = f"{s:<5}{len(r):>7}{sd:>11.3f}"
        for k in lags:
            if len(r) > k + 10:
                c = float(np.corrcoef(r[:-k], r[k:])[0, 1])
            else:
                c = float("nan")
            line += f"{c:>+9.4f}"
        lo, hi = np.quantile(r, [args.tail_q, 1.0 - args.tail_q])
        # 条件响应：给定"这一期大涨/大跌"，之后 4 期（≈1 分钟）的累计收益
        h = 4
        fut = np.convolve(r, np.ones(h), mode="full")[h:]  # fut[i] = r[i+1..i+h]
        n = min(len(r) - 1, len(fut))
        rr, ff = r[:n], fut[:n]
        up = rr >= hi
        dn = rr <= lo
        e_up = float(np.mean(ff[up])) if up.sum() > 5 else float("nan")
        e_dn = float(np.mean(ff[dn])) if dn.sum() > 5 else float("nan")
        line += f"{e_up:>+11.3f}{e_dn:>+11.3f}{int(up.sum()):>6}{int(dn.sum()):>6}"
        print(line)
        resp_up.append(e_up)
        resp_dn.append(e_dn)

    if all_r:
        R = np.concatenate(all_r)
        print("-" * (16 + 9 * len(lags) + 34))
        line = f"{'合并':<5}{len(R):>7}{float(np.std(R)):>11.3f}"
        for k in lags:
            c = float(np.corrcoef(R[:-k], R[k:])[0, 1]) if len(R) > k + 10 else float("nan")
            line += f"{c:>+9.4f}"
        line += (f"{np.nanmean(resp_up):>+11.3f}{np.nanmean(resp_dn):>+11.3f}"
                 f"{'':>6}{'':>6}")
        print(line)
        print()
        print("读法：ρ_k > 0 = 动量（涨了还会涨）；< 0 = 回复。")
        print("     E[r+|涨尾] > 0 ⇒ 大涨后继续涨 ⇒ **被动卖单**会被逆向选择 ⇒ 该把卖单挂窄(顺势) ✗逆势")
        print("     E[r+|跌尾] < 0 ⇒ 大跌后继续跌 ⇒ **被动买单**会被逆向选择 ⇒ 该把买单挂窄(顺势)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
