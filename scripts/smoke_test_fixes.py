# -*- coding: utf-8 -*-
"""修复后功能冒烟测试（只读，不改数据）。"""
import sys
import time

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")
sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena/backend")

print("=" * 60)
print("[1] funding_history 加载（真实 perp_funding）")
from backend.services.backtest_engine.funding_history import (
    load_funding_series, funding_rate_at, mean_funding_in_window,
)
series = load_funding_series("BTC", exchange="binance")
print("  binance BTC series:", "OK" if series else "None", len(series[0]) if series else "")
series_any = load_funding_series("SOL")
print("  SOL any-venue series:", "OK" if series_any else "None", len(series_any[0]) if series_any else "")
now_s = time.time()
r_now = funding_rate_at("BTC", now_s)
print(f"  BTC asof-now rate: {r_now:.6f} (default 0.0001 表示无覆盖)")
r_past = funding_rate_at("BTC", now_s - 7 * 86400)
print(f"  BTC asof-7d rate: {r_past:.6f}")
w = mean_funding_in_window("BTC", now_s - 86400, now_s)
print(f"  BTC 1d window mean |rate|: {w:.6f}")

print("=" * 60)
print("[2] derivatives_analytics 多所 Layer 0（perp_funding 优先 binance）")
from backend.services.derivatives_analytics_service import derivatives_analytics
snap = derivatives_analytics.get_snapshot("BTC")
print(f"  funding_rate={snap.funding_rate:.6f} venue={snap.funding_venue!r} "
      f"pct={snap.funding_rate_percentile:.1f} predicted={snap.predicted_funding_rate:.6f}")
print(f"  data_sources={snap.data_sources}")

print("=" * 60)
print("[3] intelligence_signal_engine 资金费率分位判定 + 汇流")
from backend.services.intelligence_signal_engine import intelligence_signal_engine
sig = intelligence_signal_engine.compute_trading_signal("BTC")
print(f"  funding: rate={sig.funding.rate:.6f} regime={sig.funding.regime} "
      f"signal={sig.funding.signal} percentile={sig.funding.percentile:.1f}")
print(f"  description: {sig.funding.description}")
print(f"  sources_available: {sig.sources_available}")
print(f"  composite: {sig.direction} conf={sig.confidence} risk={sig.risk_level}")
print(f"  prompt 片段:\n  " + "\n  ".join(sig.to_prompt_text().splitlines()[:8]))

print("=" * 60)
print("[4] onchain 因子缺数据 → 全 NaN（不再冒充中性）")
import pandas as pd
import numpy as np
from backend.services.factor_engine.factors._ai_gen_archive.onchain_factors import (
    ExchangeNetFlowFactor, WhaleTransactionFactor, TVLChangeFactor,
    ActiveAddressFactor, StablecoinMintBurnFactor,
)
empty = pd.DataFrame({"close": np.arange(50, dtype=float)})
for cls in (ExchangeNetFlowFactor, WhaleTransactionFactor, TVLChangeFactor,
            ActiveAddressFactor, StablecoinMintBurnFactor):
    out = cls().calculate(empty)
    assert out.isna().all(), f"{cls.__name__} 缺列未返回全 NaN"
    print(f"  {cls.__name__}: 缺列 → 全 NaN ✓")

# 全空列（有列名但全 NaN）也应为 NaN
empty2 = empty.copy()
empty2["exchange_net_flow"] = np.nan
out2 = ExchangeNetFlowFactor().calculate(empty2)
assert out2.isna().all()
print("  ExchangeNetFlowFactor: 全空列 → 全 NaN ✓")

# 有真实数据时正常计算
data_ok = empty.copy()
rng = np.random.default_rng(7)
data_ok["exchange_net_flow"] = np.concatenate([np.full(10, np.nan), rng.normal(0, 10, 40)])
out3 = ExchangeNetFlowFactor().calculate(data_ok)
assert not out3.isna().all() and out3.notna().sum() > 10
print(f"  ExchangeNetFlowFactor: 有数据 → 有效值 {out3.notna().sum()} 个 ✓")

print("=" * 60)
print("[5] SmartMoneyFlow 缺 whale_tx_volume → 全 NaN")
from backend.services.factor_engine.factors._ai_gen_archive.composite_factors import SmartMoneyFlowFactor
s1 = SmartMoneyFlowFactor().calculate(empty.copy())
assert s1.isna().all()
print("  archive SmartMoneyFlow: 缺列 → 全 NaN ✓")
# quarantine 副本与 archive 同源同改法；同进程导入会因 factor_id 重复注册冲突
#（生产加载器跳过 _ 目录、冷池按类加载不注册，无此问题），这里校验源码一致性：
import pathlib
_q_path = pathlib.Path(
    "D:/001Alpha/Hyper-Alpha-Arena/backend/services/factor_engine/factors/"
    "_ai_gen_quarantine/composite_factors.py"
)
_q_src = _q_path.read_text(encoding="utf-8")
assert "whale_tx_volume\" not in data.columns" in _q_src
assert "pd.Series(np.nan, index=data.index, name=\"smart_money_flow\")" in _q_src
print("  quarantine SmartMoneyFlow: 源码含相同修复 ✓")

print("=" * 60)
print("[6] whale_tracker 已无 CVD 假大单源")
from backend.services import whale_tracker_service as wts
import inspect
src = inspect.getsource(wts)
assert "def _infer_from_market_flow" not in src, "CVD 假大单源仍存在"
assert "local_cvd" not in src
print("  _infer_from_market_flow / local_cvd 已彻底移除 ✓")

print("=" * 60)
print("ALL SMOKE TESTS PASSED")
