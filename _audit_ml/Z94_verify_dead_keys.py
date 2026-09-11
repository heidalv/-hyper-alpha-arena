# -*- coding: utf-8 -*-
"""Z94: 逐键核验「死键」是否真的是死键（排除按前缀动态拼名/循环读取的假阳性）。

对每个候选键，取其"词干"在 backend/ 全仓搜索（含 f-string / 前缀常量 / 循环）并打印命中，
人工据此判定：真死 / 动态读取（假阳性）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "scripts"))
import audit_config_effective as ace  # noqa: E402

rep = ace.build_report(ROOT)
dead = rep["findings"]["env_truly_dead"]

STEMS = {
    "PC_RISK_PER_TRADE_PCT_LONG": ["PC_RISK_PER_TRADE_PCT", "RISK_PER_TRADE"],
    "LLM2_CAP_COMMITTEE": ["LLM2_CAP", "CAP_COMMITTEE"],
    "LLM2_CAP_KLINE_ANALYSIS": ["LLM2_CAP", "CAP_KLINE"],
    "LLM2_CAP_MASTER": ["LLM2_CAP", "CAP_MASTER"],
    "LLM2_CAP_REVIEW": ["LLM2_CAP", "CAP_REVIEW"],
    "LLM2_CAP_SCALP_CONFIRM": ["LLM2_CAP", "CAP_SCALP"],
    "LLM2_CAP_THESIS": ["LLM2_CAP", "CAP_THESIS"],
    "V5_MAX_TRADE_PCT": ["V5_MAX_TRADE"],
    "SCALP_TARGET_SL_PCT": ["SCALP_TARGET", "TARGET_SL"],
    "SCALP_TARGET_TP_PCT": ["SCALP_TARGET", "TARGET_TP"],
    "SCALP_MAX_HOLD_SEC": ["SCALP_MAX_HOLD"],
    "KLINE_P1_DEPTH_DAYS_15M": ["KLINE_P1_DEPTH_DAYS", "P1_DEPTH_DAYS"],
    "KLINE_RETENTION_DAYS_1M": ["KLINE_RETENTION_DAYS", "RETENTION_DAYS"],
    "ANOMALY_DETECTOR_ENABLED": ["ANOMALY_DETECTOR"],
    "ARBITRAGE_ENABLED": ["ARBITRAGE_ENABLED", "arbitrage_enabled"],
    "ARBITRAGE_MIN_ANNUAL_YIELD": ["ARBITRAGE_MIN_ANNUAL", "MIN_ANNUAL_YIELD"],
    "FACTOR_MAX_TURNOVER": ["FACTOR_MAX_TURNOVER", "max_turnover"],
    "HERMES_L2_AB_MIN_SAMPLES": ["HERMES_L2", "L2_AB_MIN"],
    "HERMES_L4_MAX_INCUBATIONS": ["HERMES_L4", "MAX_INCUBATIONS"],
    "MARKET_SCANNER_ENABLED": ["MARKET_SCANNER"],
    "MARKET_SCANNER_MIN_VOLUME": ["MARKET_SCANNER"],
    "MARKET_SCANNER_TOP_N": ["MARKET_SCANNER"],
    "ONCHAIN_DATA_ENABLED": ["ONCHAIN_DATA"],
    "ONCHAIN_GLASSNODE_KEY": ["GLASSNODE"],
    "COLD_EXCHANGE_MAX_REQ_PER_MIN_OKX": ["COLD_EXCHANGE_MAX_REQ_PER_MIN", "MAX_REQ_PER_MIN"],
    "NSGA2_POPULATION_SIZE": ["NSGA2"],
    "LOG_FILE": ["LOG_FILE"],
}

py_files = [p for p in (ROOT / "backend").rglob("*.py") if ".venv" not in str(p)]
texts = {}
for p in py_files:
    try:
        texts[p] = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        pass

for key in dead:
    stems = STEMS.get(key)
    if not stems:
        continue
    print(f"\n=== {key} ===")
    for stem in stems:
        hits = []
        for p, t in texts.items():
            for i, line in enumerate(t.splitlines(), 1):
                if stem in line:
                    hits.append((f"{p.relative_to(ROOT)}:{i}", line.strip()[:150]))
        print(f"  词干 '{stem}': 命中 {len(hits)}")
        for h, l in hits[:8]:
            print(f"     {h}  {l}")
