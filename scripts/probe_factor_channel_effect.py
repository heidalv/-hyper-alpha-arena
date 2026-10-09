# -*- coding: utf-8 -*-
"""[F356 诊断] 回测/进化路径里"因子信号"到底有没有影响成交？

发现线索：OFAT 剔除任一因子（甚至 6 个逐个剔除）后，`n_trades / sum_pnl / total_return /
win_rate / sharpe / max_drawdown` **逐位相同**。这既可能是"单因子影响太小"，
也可能是"因子通道根本没参与决策"——必须用**开关对照**而不是"剔除一个"来判定。

四组对照（同窗口、同参数，只动因子通道）：
  ① 基线            factor_signal_weight=0.3（默认）
  ② 关闭因子通道     factor_signal_weight=0.0   ← 若与①逐位相同 ⇒ 通道在本窗口零作用
  ③ 剔除全部因子     允许集清空（方向必为 0）
  ④ 正向对照         factor_signal_weight=1.0（>0.5 ⇒ 冲突时可翻转 tech_dir）
                     若④与①不同 ⇒ 通道**是通的**，只是默认权重下几乎不起作用
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
os.environ["PIPELINE_FACTOR_DIR_DISK_ENABLED"] = "0"   # 必须绕开方向缓存（F354）

from backend.services.live_pipeline_backtest_engine import (  # noqa: E402
    LivePipelineBacktestEngine, DEFAULT_PIPELINE_PARAMS, _FACTOR_DIR_CACHE,
)


class AblationEngine(LivePipelineBacktestEngine):
    def __init__(self, drop=None, **kw):
        super().__init__(**kw)
        self._drop = set(str(x) for x in (drop or ()))

    def _factor_values_at(self, i, bars):
        raw = super()._factor_values_at(i, bars)
        if raw and self._drop:
            raw = {k: v for k, v in raw.items() if str(k) not in self._drop}
        return raw


def main() -> int:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTC"
    timeframe = sys.argv[2] if len(sys.argv) > 2 else "4h"
    days = int(sys.argv[3]) if len(sys.argv) > 3 else 180
    tier = sys.argv[4] if len(sys.argv) > 4 else "mid"

    from backend.services.strategy_evolver import StrategyEvolver
    bars = StrategyEvolver()._load_bars(symbol, timeframe, days)
    print(f"K线 {len(bars)} 根  {symbol} {timeframe} {days}d tier={tier}")

    probe = AblationEngine(initial_capital=10000)
    allf = sorted(str(k) for k in (probe._factor_values_at(len(bars) - 5, bars) or {}))
    print(f"参与计算的因子 {len(allf)} 个: {allf}")

    def run(tag, *, weight=0.3, drop=None):
        g = dict(DEFAULT_PIPELINE_PARAMS)
        g["factor_signal_weight"] = weight
        _FACTOR_DIR_CACHE.clear()
        t0 = time.time()
        res = AblationEngine(drop=drop, initial_capital=10000).run(
            bars, g, tier=tier, symbol=symbol, timeframe=timeframe)
        dt = time.time() - t0
        if res is None:
            print(f"  [{tag}] None")
            return None
        sig = [(t.entry_bar, t.side, round(float(t.pnl), 6)) for t in (res.trades or [])]
        out = {
            "tag": tag, "weight": weight, "drop_n": len(drop or ()),
            "n_trades": int(res.total_trades),
            "sum_pnl": round(sum(x[2] for x in sig), 6),
            "total_return": round(float(res.total_return or 0.0), 8),
            "win_rate": round(sum(1 for x in sig if x[2] > 0) / len(sig), 4) if sig else None,
            "by_dir": {k: (v or {}).get("n") for k, v in (res.factor_attr_by_dir or {}).items()},
            "sig_hash": hash(tuple(sig)),
            "sig_head": sig[:5],
            "secs": round(dt, 1),
        }
        print(f"  [{tag}] {json.dumps({k: out[k] for k in ('n_trades','sum_pnl','total_return','win_rate','by_dir','secs')}, ensure_ascii=False)}")
        return out

    print("\n① 基线 weight=0.3")
    a = run("base_0.3", weight=0.3)
    print("\n② 关闭因子通道 weight=0.0")
    b = run("off_0.0", weight=0.0)
    print("\n③ 剔除全部因子（方向必为 0），仍用 weight=0.3")
    c = run("drop_all_0.3", weight=0.3, drop=allf)
    print("\n④ 正向对照 weight=1.0")
    d = run("pos_ctrl_1.0", weight=1.0)

    print("\n=== 结论判定 ===")
    if a and b:
        same_off = (a["sig_hash"] == b["sig_hash"] and a["total_return"] == b["total_return"])
        print(f"  ② 与 ① 成交序列是否逐位相同: {same_off}"
              f"  ⇒ {'因子通道在本窗口对成交**零作用**' if same_off else '因子通道**改变了成交**'}")
    if a and c:
        same_drop = (a["sig_hash"] == c["sig_hash"])
        print(f"  ③ 与 ① 成交序列是否逐位相同: {same_drop}"
              f"  ⇒ {'剔除全部因子也不改成交（通道确实不影响成交）' if same_drop else '剔除全部因子会改成交'}")
        print(f"     方向分桶: 基线={a['by_dir']}  剔除全部={c['by_dir']}")
    if a and d:
        diff_ctrl = (a["sig_hash"] != d["sig_hash"])
        print(f"  ④ 与 ① 成交序列是否不同: {diff_ctrl}"
              f"  ⇒ {'正向对照有效：通道**是通的**（权重>0.5 时能改决策）' if diff_ctrl else '正向对照也无效 ⇒ 通道可能整条是死的'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
