# -*- coding: utf-8 -*-
"""
全面真实验证：逐项验证本会话声称的每个修复，输出证据值。
任何一项失败都会 raise 并标注 FAIL。
"""
import inspect
import json
import sys

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")
sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena/backend")

PASS = 0
FAIL = 0


def check(name, cond, evidence):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}\n       证据: {evidence}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}\n       证据: {evidence}")
    print()


# ═══ A. 数据库实况（真实生产库查询）═══
from backend.core.tenant import system_identity
from backend.database.connection import SessionLocal, MarketSessionLocal
from sqlalchemy import text

with system_identity(), MarketSessionLocal() as mdb:
    r = mdb.execute(text(
        "SELECT COUNT(*) FROM whale_activities "
        "WHERE activity_type='large_order' AND blockchain='exchange'"
    )).scalar()
    check("A1 CVD假大单已清零", r == 0, f"large_order+blockchain=exchange 行数 = {r}")

    r = mdb.execute(text(
        "SELECT COUNT(*) FROM whale_activities WHERE timestamp IS NULL"
    )).scalar()
    check("A2 链上鲸鱼 timestamp 无 NULL", r == 0, f"timestamp IS NULL 行数 = {r}")

    r = mdb.execute(text(
        "SELECT COUNT(*) FROM whale_activities "
        "WHERE activity_type='aggregate_whale' AND created_at > NOW() - INTERVAL '30 minutes'"
    )).scalar()
    check("A3 聚合鲸鱼采集仍在实时写入", r > 0, f"近30分钟 aggregate_whale 行数 = {r}")

    r = mdb.execute(text(
        "SELECT exchange, ROUND((EXTRACT(EPOCH FROM NOW())*1000 - MAX(timestamp))/60000.0, 1) "
        "FROM perp_funding WHERE exchange IN ('binance','asterdex','bybit','okx') "
        "GROUP BY exchange ORDER BY exchange"
    )).fetchall()
    fresh = all(float(x[1]) < 30 for x in r)
    check("A4 多所资金费率新鲜（<30分钟）", fresh, f"各所滞后(分钟): {r}")

with system_identity(), SessionLocal() as cdb:
    r = cdb.execute(text(
        "SELECT COUNT(*) FROM full_auto_sessions WHERE session_id='fa_185f162052' AND arb_enabled=true"
    )).scalar()
    check("A5 实盘会话 arb 未开启（保持 paper 语义）", r == 0, f"实盘会话 arb_enabled=true 行数 = {r}")

# ═══ B. 代码级验证（源码事实）═══
import backend.services.whale_tracker_service as wts
src = inspect.getsource(wts)
check("B1 CVD 假大单源已删除", "def _infer_from_market_flow" not in src and "local_cvd" not in src,
      "源码中不存在 _infer_from_market_flow / local_cvd")

from backend.services.arbitrage.live_executor import LiveExecutor
src2 = inspect.getsource(LiveExecutor.execute_funding)
check("B2 方向感知 wash 守卫存在", "wash_guard_opposing_position_on_asterdex" in src2,
      "execute_funding 含 opposing 守卫分支")
check("B3 杠杆跟随策略配置", "leverage = max(1.0, float(payload.get(\"leverage\"" in src2,
      "execute_funding 从 payload 读 leverage")

from backend.services.factor_engine.midlong_active_factor_set import MidLongActiveFactorSet
src3 = inspect.getsource(MidLongActiveFactorSet.recheck_and_prune)
check("B4 heldout reject 计入失败计数（治理修复）",
      src3.count('str(_ho.get("verdict")) == "reject"') == 2,
      f"recheck_and_prune 中 heldout reject 判定出现 {src3.count(chr(34)+'reject'+chr(34))} 次（预期≥2）")

from backend.services.evolution.gp_miner import GPConfig
check("B5 GP 挖掘目标函数已对齐 ICIR", GPConfig().objective == "icir",
      f"GPConfig().objective = {GPConfig().objective!r}")

from backend.config.arb_config_loader import arb_config
check("B6 套利杠杆来自策略配置", float(arb_config.funding.leverage) == 3.0,
      f"arb_config.funding.leverage = {arb_config.funding.leverage}")

# ═══ C. 行为级验证（真实计算）═══
from backend.services.decision_fusion_arbiter import _pwin_tiers, decide_scalp
tiers = _pwin_tiers()
check("C1 pwin 0.50-0.55 负EV档已移除", all(t[0] >= 0.55 for t in tiers), f"当前分档 = {tiers}")
d = decide_scalp(pwin=0.52, factor_score=60, direction="long", tp_pct=0.01, sl_pct=0.005)
check("C2 pwin=0.52 不再按常规档入场", not d.allowed or d.size_mult <= 0.25,
      f"pwin=0.52 → action={d.action} size={d.size_mult}")

from backend.services.intelligence_signal_engine import (
    IntelligenceSignalEngine, TradingDirectionSignal, FundingRegime,
)
eng = IntelligenceSignalEngine()
sig = TradingDirectionSignal(symbol="TEST")
sig.funding = FundingRegime(rate=0.0012, regime="extreme_positive", signal="bearish", description="x", percentile=98.0)
sig.sources_available = {k: (k == "funding") for k in (
    "funding", "oi", "liquidation", "whale", "news", "sentiment", "ls_ratio", "top_trader")}
eng._compute_confluence(sig)
check("C3 汇流权重不再被缺失组件稀释", sig.direction == "bearish" and sig.confidence == 100,
      f"仅 funding 可用 → direction={sig.direction} confidence={sig.confidence}（应 bearish/100）")

import numpy as np
import pandas as pd
from backend.services.factor_engine.factors._ai_gen_archive.onchain_factors import ExchangeNetFlowFactor
empty = pd.DataFrame({"close": np.arange(40, dtype=float)})
out = ExchangeNetFlowFactor().calculate(empty)
check("C4 链上因子缺数据返回 NaN（不冒充中性）", out.isna().all(), f"缺列时输出全 NaN = {out.isna().all()}")

from backend.services.factor_engine.base_factors import factor_engine
fake = pd.DataFrame({"open": [100.0]*40, "high": [101.0]*40, "low": [99.0]*40,
                     "close": [100.5]*40, "volume": [1000.0]*40})
vals = factor_engine.compute_all_factors(fake)
check("C5 信号计算已收敛到受治理因子（不再是 141 个未验证因子）",
      len(vals) <= 10 and len(vals) > 0,
      f"无 allowlist 调用计算结果数 = {len(vals)}（修复前=141），ids={sorted(vals.keys())[:8]}")

from backend.services.arbitrage.orchestrator import arbitrage_orchestrator
from backend.services.arbitrage.opportunity_scanner import ArbitrageOpportunity, FundingRateSnapshot
opp = ArbitrageOpportunity(
    opportunity_id="v", symbol="BTC", strategy="funding_short", expected_annual_yield=0.30,
    funding_snapshot=FundingRateSnapshot(symbol="BTC", current_rate=0.0003, predicted_rate=0.0,
                                         rate_8h_avg=0.0003, rate_24h_avg=0.0003, annual_yield=0.30,
                                         oi_total=1e6, volume_24h=1e9),
    recommended_size=0.0, risk_score=0.3, confidence=0.7, timestamp=0.0)
kept = arbitrage_orchestrator._apply_funding_pair_spread([opp])
check("C6 双所价差口径：当前无机会时诚实剔除", len(kept) == 0,
      f"BTC 假机会经价差修正后保留数 = {len(kept)}（当前真实价差年化<6%，应剔除）")

store = json.loads(open("D:/001Alpha/Hyper-Alpha-Arena/data/discovered_factors.json", encoding="utf-8").read())
cold_n = len([k for k in store if k.startswith("t326:cold_")])
check("C7 冷池过线因子已登记候选", cold_n >= 20, f"cold_* 候选数 = {cold_n}")

from backend.services.ai_decision_integration import build_factor_guidance_for_prompt
gtext = build_factor_guidance_for_prompt(["BTC"], {"BTC": fake}, {"BTC": 100.5})
check("C8 LLM 引导包含活跃集 SSOT", "量化层活跃因子集" in gtext,
      f"引导文本含活跃集段落 = {'量化层活跃因子集' in gtext}")

# ═══ D. 在线 API（真实后端进程）═══
import requests
base = "http://127.0.0.1:8000"
P = {"http": None, "https": None}

r1 = requests.get(f"{base}/api/full-auto/tier-activity/fa_7e12e7a1b6", timeout=45, proxies=P).json()
check("D1 tier-activity 三列有数据（F36 每周期配额修复）",
      len(r1.get("short", [])) > 0 and len(r1.get("mid", [])) > 0 and len(r1.get("long", [])) > 0,
      f"short={len(r1.get('short', []))} mid={len(r1.get('mid', []))} long={len(r1.get('long', []))}")

r2 = requests.get(f"{base}/api/rebate/funding-matrix", timeout=45, proxies=P).json()
check("D2 funding-matrix 含 arb_status + 多所矩阵",
      "arb_status" in r2 and r2.get("venue_count", 0) >= 4,
      f"venue_count={r2.get('venue_count')} symbol_count={r2.get('symbol_count')} arb_status={'arb_status' in r2}")

r3 = requests.get(f"{base}/api/rebate/asterdex-points/summary", timeout=45, proxies=P).json()
check("D3 积分账本 API 在线（含开关策略）",
      "policy" in r3 and "summary" in r3,
      f"policy.enabled={r3.get('policy', {}).get('enabled')} reason={r3.get('policy', {}).get('reason')}")

r4 = requests.get(f"{base}/api/intelligence/trading-signal/BTC", timeout=45, proxies=P).json()
f = r4.get("funding") or {}
check("D4 情报信号资金费率含真实分位（非死字段）",
      f.get("percentile") is not None and f.get("rate") is not None,
      f"rate={f.get('rate')} percentile={f.get('percentile')} regime={f.get('regime')}")

print("=" * 60)
print(f"结果: {PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
