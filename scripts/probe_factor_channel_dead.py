# -*- coding: utf-8 -*-
"""[F356 判定] 逐 bar 功能探针：把因子方向强制成 +1/-1/0，看 `_pipeline_signal` 的
返回值是否**曾经**不同。

为什么需要它：整轮回测对照（weight=0.0/0.3/1.0、剔除全部因子）四组成交**逐位相同**，
但那只覆盖"最终成交"这一层，可能被巧合掩盖。本探针直接打在信号函数上，
逐 bar 比较，若全部相同即为**功能性死通道**（与窗口无关）。

链路（`live_pipeline_backtest_engine.py`）：
    :934 replay_finalize(...) -> `if action != "enter": return None`   ← 先决定"是否开仓"
    :941 factor_dir = self._compute_factor_direction(...)              ← 开仓后才算因子
    :949-985 tech/flow/sent + 因子融合（冲突时需 weight>0.5 才翻转 tech_dir）
    :987 replay_confirmation(tech_dir, flow_dir, sent_dir, min_dims)
    :992 replay_rule_decision(confirm_action, confirm_dir, mid_bias, mid_conf)
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
os.environ["PIPELINE_FACTOR_DIR_DISK_ENABLED"] = "0"

import numpy as np  # noqa: E402

from backend.services.live_pipeline_backtest_engine import (  # noqa: E402
    LivePipelineBacktestEngine, DEFAULT_PIPELINE_PARAMS, _calc_rsi, _calc_macd,
)


class ForceFactor(LivePipelineBacktestEngine):
    """把因子方向钉死成指定值，其它一切不变。"""

    def __init__(self, forced=0, **kw):
        super().__init__(**kw)
        self._forced = int(forced)

    def _compute_factor_direction(self, i, bars, p):
        return self._forced


def probe(symbol: str, timeframe: str, days: int, tier: str, weight: float = 1.0,
          funding=None, fgi=None) -> dict:
    from backend.services.strategy_evolver import StrategyEvolver
    bars = StrategyEvolver()._load_bars(symbol, timeframe, days)
    if len(bars) < 120:
        return {"symbol": symbol, "timeframe": timeframe, "bars": len(bars), "skip": True}
    p = dict(DEFAULT_PIPELINE_PARAMS)
    p["factor_signal_weight"] = weight
    closes = np.array([b.c for b in bars], dtype=np.float64)
    rsi_arr = _calc_rsi(closes, 14)
    macd_line, _ = _calc_macd(closes)

    eng = {d: ForceFactor(forced=d, initial_capital=10000) for d in (1, -1, 0)}
    diffs = 0
    non_none = 0
    detail = []
    for i in range(30, len(bars)):
        sigs = {d: e._pipeline_signal(i, bars, rsi_arr, macd_line, p,
                                      funding or {}, fgi or {})
                for d, e in eng.items()}
        if sigs[1] is not None or sigs[-1] is not None or sigs[0] is not None:
            non_none += 1
        if len(set(sigs.values())) > 1:
            diffs += 1
            if len(detail) < 5:
                detail.append({"bar": i, "sig": {str(k): v for k, v in sigs.items()}})
    return {"symbol": symbol, "timeframe": timeframe, "tier": tier, "weight": weight,
            "bars": len(bars), "evaluated": len(bars) - 30, "non_none": non_none,
            "diff_bars": diffs, "detail": detail,
            "funding_n": len(funding or {}), "fgi_n": len(fgi or {})}


def main() -> int:
    print("=" * 78)
    print("F356 逐 bar 功能探针：因子方向对 `_pipeline_signal` 返回值的影响")
    print("=" * 78)

    # [关键] 必须带上**真实** funding/FGI：`replay_confirmation` 要求
    # (tech, flow, sent) 里至少 confirmation_min_dims(=2) 个非零且同向；
    # flow/sent 分别由资金费率与恐贪指数决定。若传空 map，二者恒为 0 ⇒
    # 确认永远 HOLD ⇒ 因子方向**在构造上**不可能影响决策（会得出假的"死通道"结论）。
    funding, fgi = {}, {}
    try:
        from backend.database.connection import SessionLocal
        from backend.services.strategy_evolver import StrategyEvolver
        db = SessionLocal()
        try:
            funding = StrategyEvolver._load_funding_rates(db, ["BTC"]) or {}
            fgi = StrategyEvolver._load_fgi_series(db, 365) or {}
        finally:
            db.close()
    except Exception as e:
        print("  真实 funding/FGI 载入失败:", str(e)[:140])
    print(f"真实序列: funding={len(funding)} 条  fgi={len(fgi)} 条")

    cases = [("BTC", "4h", 180), ("ETH", "4h", 180), ("BTC", "1h", 90), ("SOL", "4h", 120)]
    total_diff = 0
    rows = []
    for sym, tf, days in cases:
        for w in (1.0, 0.3):
            try:
                r = probe(sym, tf, days, "mid", weight=w, funding=funding, fgi=fgi)
            except Exception as e:
                print(f"  {sym} {tf} {days}d w={w}: 失败 {str(e)[:80]}")
                continue
            if r.get("skip"):
                print(f"  {sym} {tf} {days}d: K线不足({r['bars']})")
                continue
            rows.append(r)
            total_diff += r["diff_bars"]
            print(f"  {sym} {tf} {days}d w={r['weight']}: bars={r['bars']} 评估={r['evaluated']} "
                  f"有信号bar={r['non_none']} **方向不同的bar={r['diff_bars']}**")
            for d in r["detail"]:
                print(f"      差异样例 bar={d['bar']} sig={d['sig']}")

    print("\n=== 判定（带真实 funding/FGI）===")
    if total_diff == 0:
        print("  **即使带上真实 funding/FGI，因子方向被强制成 +1/-1/0 时 `_pipeline_signal` "
              "返回值在所有 bar 上仍完全一致**")
        print("  ⇒ 回测/进化路径里因子通道**功能性死亡**：算得出方向，但永远不改变任何决策。")
        print("  ⇒ 推论：进化引擎的 fitness 对因子体系**完全不敏感**。")
    else:
        print(f"  共 {total_diff} 个 bar 的返回值随因子方向变化 ⇒ 通道是通的（前面的"
              f"『四组成交逐位相同』是空 funding/FGI 造成的假象）")
    for r in rows:
        print(f"    {r['symbol']} {r['timeframe']} w={r['weight']}: diff={r['diff_bars']}/{r['evaluated']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
