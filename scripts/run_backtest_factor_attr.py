# -*- coding: utf-8 -*-
"""[F345/B4 实跑] 用**实盘同款管线**跑回测并产出**因子名级归因**（首批真实证据）。

回答用户第 4 项诉求的核心两问：
  「这套参数赚不赚钱」→ 已有；
  「**是哪些因子赚的**」→ 本脚本首次产出具名归因（B4 phase 2）。

只跑回测、只写 `data/backtest_factor_attr.jsonl` 与因子方向缓存；
**不碰实盘下单链路、不改 .env、不重启**。

用法：
    python scripts/run_backtest_factor_attr.py [symbol] [timeframe] [days] [tier]
默认：BTC 4h 240 天 mid
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

# 必须在 import 引擎之前打开归因开关（模块级读取）
os.environ.setdefault("BACKTEST_FACTOR_ATTR", "1")


def main() -> int:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTC"
    timeframe = sys.argv[2] if len(sys.argv) > 2 else "4h"
    days = int(sys.argv[3]) if len(sys.argv) > 3 else 240
    tier = sys.argv[4] if len(sys.argv) > 4 else "mid"

    print("=" * 78)
    print(f"实盘同款管线回测 · 因子名级归因  symbol={symbol} tf={timeframe} days={days} tier={tier}")
    print(f"BACKTEST_FACTOR_ATTR={os.environ.get('BACKTEST_FACTOR_ATTR')}")
    print("=" * 78)

    from backend.services.strategy_evolver import StrategyEvolver
    from backend.services.live_pipeline_backtest_engine import (
        LivePipelineBacktestEngine, DEFAULT_PIPELINE_PARAMS, factor_attr_path,
    )

    ev = StrategyEvolver()
    t0 = time.time()
    bars = ev._load_bars(symbol, timeframe, days)
    print(f"K线: {len(bars)} 根（加载 {time.time() - t0:.1f}s）")
    if len(bars) < 60:
        print("K线不足，退出")
        return 1

    genome = dict(DEFAULT_PIPELINE_PARAMS)
    t0 = time.time()
    res = LivePipelineBacktestEngine(initial_capital=10000).run(
        bars, genome, tier=tier, symbol=symbol, timeframe=timeframe,
    )
    dur = time.time() - t0
    if res is None:
        print("回测返回 None（引擎内部异常或跳过）")
        return 1
    print(f"运行 {dur:.1f}s  run_id={res.run_id}  error={getattr(res, 'error', None)}")
    print(f"\n【这套参数赚不赚钱】")
    print(f"  交易数={res.total_trades}  胜率={getattr(res, 'win_rate', None)}  "
          f"总收益={getattr(res, 'total_return', None)}  Sharpe={getattr(res, 'sharpe_ratio', None)}  "
          f"最大回撤={getattr(res, 'max_drawdown', None)}")

    print(f"\n【B4 phase 1 · 方向级归因】三桶 n 之和必须 == 交易数")
    bd = getattr(res, "factor_attr_by_dir", None) or {}
    tot_n = 0
    for k in ("1", "-1", "0"):
        row = bd.get(k) or bd.get(int(k)) or {}
        n = row.get("n", 0)
        tot_n += n
        print(f"  因子方向 {k:>2}: n={n:<5} 胜率={row.get('win_rate')} "
              f"avg_pnl={row.get('avg_pnl')} avg_bp={row.get('avg_bp')}")
    print(f"  三桶合计={tot_n}  交易数={res.total_trades}  "
          f"{'一致 ✓' if tot_n == res.total_trades else '**不一致**'}")

    print(f"\n【B4 phase 2 · 因子名级归因（覆盖率口径，非增量 alpha）】")
    cov = getattr(res, "factor_attr_coverage", None) or {}
    if cov:
        print(f"  旁路覆盖率: covered={cov.get('covered')}/{cov.get('needed')} "
              f"（按需补算 {cov.get('computed')} 个窗口）")
    bn = getattr(res, "factor_attr_by_name", None) or {}
    if not bn:
        print("  空 —— 未产出具名归因（检查 BACKTEST_FACTOR_ATTR / 旁路是否拿到因子值）")
    else:
        # 字段口径 = attribute_trades_by_factor 的返回：n_long/n_short + avg_bp_long/avg_bp_short
        # （**没有** `n`/`win_rate` 键；此前本脚本按 n/win_rate 打印，于是真数据也显示成 0/None）
        rows = [(name, int(v.get("n_long") or 0), int(v.get("n_short") or 0),
                 v.get("avg_bp_long"), v.get("avg_bp_short"), v.get("coverage"))
                for name, v in bn.items() if isinstance(v, dict)]
        print(f"  因子数={len(rows)}")
        rows.sort(key=lambda r: (r[3] if r[3] is not None else 0), reverse=True)
        print("  —— 多头侧 avg_bp 最高 6 个 ——")
        for name, nl, ns, bl, bs, cv in rows[:6]:
            print(f"    {name:<36} n_long={nl:<4} n_short={ns:<4} "
                  f"avg_bp_long={bl}  avg_bp_short={bs}  coverage={cv}")
        print("  —— 多头侧 avg_bp 最低 6 个 ——")
        for name, nl, ns, bl, bs, cv in rows[-6:]:
            print(f"    {name:<36} n_long={nl:<4} n_short={ns:<4} "
                  f"avg_bp_long={bl}  avg_bp_short={bs}  coverage={cv}")
        print("  ⚠️ 口径提醒：这是**覆盖度归因**（该因子在开仓 bar 活跃的那批交易的盈亏），"
              "无对照组/无反事实，**不是增量 alpha**；样本少时不可据此判定因子优劣。")

    p = factor_attr_path()
    print(f"\n【落盘】{p}")
    print(f"  存在={Path(p).exists()}  大小={Path(p).stat().st_size if Path(p).exists() else 0} B")
    if Path(p).exists():
        lines = Path(p).read_text(encoding="utf-8").strip().splitlines()
        print(f"  行数={len(lines)}")
        if lines:
            head = json.loads(lines[-1])
            print(f"  末行键: {sorted(head.keys())}  run_id={head.get('run_id')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
