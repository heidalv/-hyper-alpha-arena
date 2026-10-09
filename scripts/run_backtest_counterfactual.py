# -*- coding: utf-8 -*-
"""[F355 2026-09-18] 回测**增量（反事实）因子归因** —— 单因子剔除消融（OFAT ablation）。

## 为什么需要它（B4 phase 2 的最后一环）

已有两级别归因：
  - phase 1 方向级：按开仓当根因子方向分桶（回答"哪个方向赚了"）；
  - phase 2 覆盖度级：`attribute_trades_by_factor`（回答"该因子活跃的那批交易赚了没"）。
两者都**不是增量 alpha**（没有对照组）。要回答"**去掉这个因子，结果变好还是变坏**"，
只能做反事实：把因子从计算集合里剔除后重跑同一条实盘管线，比较结果。

## 必须先绕开的一个坑（F354，本次实测发现）

因子**方向序列**缓存（内存 `_FACTOR_DIR_CACHE` + 磁盘 `data/factor_dir_cache/*.json`）
的键只有 `(symbol, timeframe, ts0, n)` + `gov/full` **模式标签**（`_factor_dir_mode_tag()`），
**不含因子集合本身**。因此在缓存命中的情况下，"剔除因子 F 重跑"会**原样复用基线方向序列**
⇒ 结果与基线逐位相同 ⇒ 看起来像"该因子增量为 0"。这是**假零**，不是结论。
本脚本因此强制：
    `PIPELINE_FACTOR_DIR_DISK_ENABLED=0` + 每次 run 前 `_FACTOR_DIR_CACHE.clear()`
（实测代价可接受：受治理因子集约 6 个，91 根 bar 未缓存仅 1.4s）。

## 口径声明（不可省略）

- 这是 **OFAT（一次只动一个因子）消融**，**不是 Shapley**：因子间存在交互，
  各因子的"增量"**不相加等于总收益**；
- 参考回放用 `DEFAULT_PIPELINE_PARAMS`（与 `parity_score` 同口径）⇒ 数值只在该参数集下成立；
- 单窗口、单品种、默认参数 ⇒ **只用于排序与方向判断，不足以做统计显著性结论**。

用法：
    python scripts/run_backtest_counterfactual.py [symbol] [timeframe] [days] [tier]
默认：BTC 4h 180 mid
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
# 仅在作为脚本运行时接管 stdout：模块级包装会在测试进程里破坏 pytest 的捕获
# （`ValueError: I/O operation on closed file`），因此本模块必须可安全 import。
if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 必须在 import 引擎之前关掉磁盘缓存（引擎在**模块导入时**读取该 env）。
# 仅在作为脚本运行时设置，避免污染导入本模块的测试进程。
if __name__ == "__main__":
    os.environ["PIPELINE_FACTOR_DIR_DISK_ENABLED"] = "0"

from backend.services.live_pipeline_backtest_engine import (  # noqa: E402
    LivePipelineBacktestEngine, DEFAULT_PIPELINE_PARAMS, _FACTOR_DIR_CACHE,
)

OUT_PATH = ROOT / "data" / "backtest_counterfactual.jsonl"


class AblationEngine(LivePipelineBacktestEngine):
    """按需剔除指定因子键后再算因子值（同一条实盘管线，只改因子集合）。"""

    def __init__(self, drop=None, **kw):
        super().__init__(**kw)
        self._drop = set(str(x) for x in (drop or ()))

    def _factor_values_at(self, i, bars):
        raw = super()._factor_values_at(i, bars)
        if raw and self._drop:
            raw = {k: v for k, v in raw.items() if str(k) not in self._drop}
        return raw


def _metrics(res) -> dict:
    trades = list(getattr(res, "trades", []) or [])
    pnl = sum(float(getattr(t, "pnl", 0.0) or 0.0) for t in trades)
    wins = sum(1 for t in trades if float(getattr(t, "pnl", 0.0) or 0.0) > 0)
    return {
        "n_trades": int(getattr(res, "total_trades", 0) or 0),
        "sum_pnl": round(pnl, 6),
        "total_return": round(float(getattr(res, "total_return", 0.0) or 0.0), 6),
        "win_rate": round(wins / len(trades), 4) if trades else None,
        "sharpe": round(float(getattr(res, "sharpe_ratio", 0.0) or 0.0), 4),
        "max_drawdown": round(float(getattr(res, "max_drawdown", 0.0) or 0.0), 4),
    }


def incremental_table(base: dict, runs: dict) -> list:
    """纯函数：由基线与各剔除运行算出增量（便于单测）。

    增量定义：`base - without`（正 = 该因子有正贡献）。
    """
    rows = []
    for fid, m in runs.items():
        if not m:
            rows.append({"factor": fid, "available": False})
            continue
        rows.append({
            "factor": fid,
            "available": True,
            "d_sum_pnl": round(base["sum_pnl"] - m["sum_pnl"], 6),
            "d_trades": base["n_trades"] - m["n_trades"],
            "d_total_return": round(base["total_return"] - m["total_return"], 6),
            "base_win_rate": base["win_rate"],
            "without_win_rate": m["win_rate"],
        })
    rows.sort(key=lambda r: r.get("d_sum_pnl", 0.0), reverse=True)
    return rows


def main() -> int:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTC"
    timeframe = sys.argv[2] if len(sys.argv) > 2 else "4h"
    days = int(sys.argv[3]) if len(sys.argv) > 3 else 180
    tier = sys.argv[4] if len(sys.argv) > 4 else "mid"

    print("=" * 78)
    print(f"增量（反事实）因子归因 · OFAT 消融  {symbol} {timeframe} {days}d tier={tier}")
    print(f"磁盘方向缓存已禁用：PIPELINE_FACTOR_DIR_DISK_ENABLED="
          f"{os.environ.get('PIPELINE_FACTOR_DIR_DISK_ENABLED')}")
    print("=" * 78)

    from backend.services.strategy_evolver import StrategyEvolver

    ev = StrategyEvolver()
    bars = ev._load_bars(symbol, timeframe, days)
    print(f"K线 {len(bars)} 根")
    if len(bars) < 100:
        print("K线不足")
        return 1
    genome = dict(DEFAULT_PIPELINE_PARAMS)

    # [F356 关键修正] 必须传**真实** funding/FGI，否则消融结果是假零：
    # `_pipeline_signal` 里 flow_dir/sent_dir 完全由资金费率与恐贪决定，
    # 而 `replay_confirmation` 要求 (tech, flow, sent) 至少 confirmation_min_dims(=2)
    # 个非零且同向；空 map ⇒ flow=sent=0 ⇒ 永远 HOLD ⇒ 方向退化为 mid_bias，
    # **因子方向在构造上无法影响任何决策** ⇒ 剔除任何因子结果都逐位相同（假零）。
    funding, fgi = {}, {}
    try:
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        try:
            funding = StrategyEvolver._load_funding_rates(db, [symbol]) or {}
            fgi = StrategyEvolver._load_fgi_series(db, max(days, 365)) or {}
        finally:
            db.close()
    except Exception as e:
        print("  !! funding/FGI 载入失败，消融结果将是假零:", str(e)[:140])
    print(f"真实序列: funding={len(funding)} 条  fgi={len(fgi)} 条")
    if not funding and not fgi:
        print("  !! 两者皆空 ⇒ 本次消融**不可信**（因子维度结构性失效），拒绝出结论")
        return 2

    def run(drop=None, tag=""):
        _FACTOR_DIR_CACHE.clear()          # 关键：不得复用基线方向序列（F354）
        t0 = time.time()
        res = AblationEngine(drop=drop, initial_capital=10000).run(
            bars, dict(genome), tier=tier, symbol=symbol, timeframe=timeframe,
            funding_rate_series=funding, fgi_series=fgi)
        dt = time.time() - t0
        if res is None or getattr(res, "error", None):
            print(f"  [{tag}] 运行失败: {getattr(res, 'error', 'None')}")
            return None, dt
        return _metrics(res), dt

    print("\n① 基线（全因子集）")
    base, dt0 = run(None, "base")
    if not base:
        return 1
    print(f"   {json.dumps(base, ensure_ascii=False)}  （{dt0:.1f}s）")

    print("\n② 发现本次实际参与计算的因子键")
    probe = AblationEngine(initial_capital=10000)
    raw = probe._factor_values_at(len(bars) - 5, bars) or {}
    fids = sorted(str(k) for k in raw)
    print(f"   {len(fids)} 个: {fids}")
    if not fids:
        print("   无因子值（allowlist 为空？）——无消融对象")
        return 1

    print("\n③ 逐个剔除后重跑（每次都是完整管线，缓存已绕开）")
    runs = {}
    for fid in fids:
        m, dt = run({fid}, fid)
        runs[fid] = m
        print(f"   剔除 {fid:<34} -> {json.dumps(m, ensure_ascii=False) if m else 'None'}  ({dt:.1f}s)")

    # [F357] 同一底层因子会以两个名字各出现一次（`evo_<hash>` 与 `evo_s5m_<hash>`，
    # 见 `base_factors` 的键归一化）。只剔除其中一个时另一个仍在投票 ⇒
    # 测出的增量**系统性偏小**。故按"去掉前缀后的尾段"分组，补跑"整组剔除"。
    def _group_key(k: str) -> str:
        s = str(k)
        for pre in ("evo_s5m_", "evo_"):
            if s.startswith(pre):
                return s[len(pre):]
        return s

    groups = {}
    for fid in fids:
        groups.setdefault(_group_key(fid), []).append(fid)
    dup_groups = {g: v for g, v in groups.items() if len(v) > 1}
    if dup_groups:
        print(f"\n③b 同名多副本（只剔一个会低估贡献，故按组再剔一次）: "
              f"{ {g: v for g, v in dup_groups.items()} }")
        for g, members in dup_groups.items():
            tag = f"group:{g}"
            m, dt = run(set(members), tag)
            runs[tag] = m
            print(f"   剔除整组 {members} -> "
                  f"{json.dumps(m, ensure_ascii=False) if m else 'None'}  ({dt:.1f}s)")

    print("\n④ 增量表（base − without；正 = 该因子有正贡献）")
    rows = incremental_table(base, runs)
    print(f"   {'因子':<36}{'Δsum_pnl':>12}{'Δtrades':>9}{'Δ总收益':>12}{'基线胜率':>10}{'剔除后胜率':>11}")
    for r in rows:
        if not r.get("available"):
            print(f"   {r['factor']:<36}{'— 运行失败':>12}")
            continue
        print(f"   {r['factor']:<36}{r['d_sum_pnl']:>12.4f}{r['d_trades']:>9}"
              f"{r['d_total_return']:>12.4f}{str(r['base_win_rate']):>10}{str(r['without_win_rate']):>11}")

    try:
        rec = {"symbol": symbol, "timeframe": timeframe, "days": days, "tier": tier,
               "ts": time.time(), "baseline": base, "without": runs, "incremental": rows,
               "method": "OFAT ablation (NOT Shapley; increments do not sum to total)",
               "params": "DEFAULT_PIPELINE_PARAMS", "cache": "bypassed (F354)",
               # [F355 完整性标记] 没有这两条序列时因子维度**构造性失效**，
               # 全部增量恒为 0（假零）。落盘必须自带该标记，否则后人无法分辨
               # "这批记录是有效结论"还是"当年的假零"（最早的记录缺此字段=不可信）。
               "funding_fgi": {"funding_n": len(funding), "fgi_n": len(fgi),
                               "valid": bool(funding and fgi)}}
        with open(OUT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        print(f"\n【落盘】{OUT_PATH}")
    except Exception as e:
        print("落盘失败:", str(e)[:120])
    print("\n⚠️ 口径：OFAT 消融、单窗口、DEFAULT_PIPELINE_PARAMS ⇒ 只可用于排序/方向判断，"
          "各因子增量**不相加等于总收益**（存在交互），也不足以做显著性结论。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
