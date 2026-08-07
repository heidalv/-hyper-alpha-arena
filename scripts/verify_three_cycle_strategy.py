#!/usr/bin/env python3
"""三周期策略链路自检。

用途：
  重启后快速确认短/中/长期策略是否吃到正确周期数据，尤其验证：
  - 中线是否默认由 SwingAgent 独立跑，不被 MLTO 覆盖
  - 中期是否有 1d 背景 + 4h 主判断 + 1h 择时
  - 长期是否有 LTP + 1d + 1w 周线确认

运行：
  python scripts/verify_three_cycle_strategy.py --symbol BTC
  python scripts/verify_three_cycle_strategy.py --no-live
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# 与 main.py 一致加载 .env，确保静态检查读到真实配置
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
except Exception:
    pass


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


class Reporter:
    def __init__(self) -> None:
        self.results: list[CheckResult] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append(CheckResult(name, ok, detail))
        mark = "PASS" if ok else "FAIL"
        suffix = f" - {detail}" if detail else ""
        print(f"[{mark}] {name}{suffix}")

    def warn(self, name: str, detail: str = "") -> None:
        self.results.append(CheckResult(name, True, f"WARN: {detail}"))
        suffix = f" - {detail}" if detail else ""
        print(f"[WARN] {name}{suffix}")

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.results if not r.ok)


def _has_all(mapping: dict[str, Any], keys: list[str]) -> tuple[bool, str]:
    missing = [k for k in keys if mapping.get(k) is None]
    return not missing, ("missing=" + ",".join(missing) if missing else "ok")


def static_checks(rep: Reporter) -> None:
    from backend.config import settings as s

    rep.check(
        "中线默认不走 MLTO",
        not getattr(s, "MIDLONG_MID_VIA_MLTO", False),
        f"MIDLONG_MID_VIA_MLTO={getattr(s, 'MIDLONG_MID_VIA_MLTO', None)}",
    )
    rep.check(
        "中线/长线默认 Agent 直控（MLTO 不控开单）",
        not getattr(s, "MIDLONG_MLTO_CONTROLS_EXEC", True),
        f"MIDLONG_MLTO_CONTROLS_EXEC={getattr(s, 'MIDLONG_MLTO_CONTROLS_EXEC', None)}",
    )
    rep.check(
        "MLTO open_gate 默认关闭",
        not getattr(s, "MIDLONG_THESIS_OPEN_GATE", True),
        f"MIDLONG_THESIS_OPEN_GATE={getattr(s, 'MIDLONG_THESIS_OPEN_GATE', None)}",
    )
    rep.check(
        "编排器软注入（非硬门控）",
        not getattr(s, "ORCHESTRATOR_HARD_GATE", True),
        f"ORCHESTRATOR_HARD_GATE={getattr(s, 'ORCHESTRATOR_HARD_GATE', None)}",
    )
    _mid_tick = int(getattr(s, "TIER_MID_AI_TICK_SEC", 999) or 999)
    _long_tick = int(getattr(s, "TIER_LONG_AI_TICK_SEC", 999) or 999)
    rep.check(
        "中线/长线 tick 已对齐 Scalp 频率",
        _mid_tick <= 60 and _long_tick <= 120,
        f"TIER_MID_AI_TICK_SEC={_mid_tick}, TIER_LONG_AI_TICK_SEC={_long_tick}",
    )
    rep.check(
        "中线/长线 AI 强制调度",
        bool(getattr(s, "MIDLONG_AI_MANDATORY", False)),
        f"MIDLONG_AI_MANDATORY={getattr(s, 'MIDLONG_AI_MANDATORY', None)}",
    )
    rep.check(
        "mid/long 独立调度循环",
        bool(getattr(s, "MIDLONG_AGENT_INDEPENDENT_SCHEDULER", False)),
        f"MIDLONG_AGENT_INDEPENDENT_SCHEDULER={getattr(s, 'MIDLONG_AGENT_INDEPENDENT_SCHEDULER', None)}",
    )
    rep.check(
        "Master 委托 mid/long 新开给独立循环",
        bool(getattr(s, "MIDLONG_MASTER_DELEGATE", False)),
        f"MIDLONG_MASTER_DELEGATE={getattr(s, 'MIDLONG_MASTER_DELEGATE', None)}",
    )
    rep.check(
        "Live 宪法风控默认开启",
        bool(getattr(s, "LIVE_CONSTITUTIONAL_RISK_ENABLED", False)),
        f"LIVE_CONSTITUTIONAL_RISK_ENABLED={getattr(s, 'LIVE_CONSTITUTIONAL_RISK_ENABLED', None)}",
    )
    try:
        from backend.services.full_auto_trading_service import FullAutoTradingService
        svc = FullAutoTradingService()
        rep.check(
            "LiveConstitutional 方法已挂载",
            callable(getattr(svc, "_live_constitutional_pre_trade_check", None))
            and callable(getattr(svc, "_check_live_constitutional_session_risk", None)),
        )
    except Exception as exc:
        rep.check("LiveConstitutional 方法已挂载", False, str(exc))
    rep.check(
        "FactGuard paper shadow",
        not getattr(s, "AGENT_FACT_GUARD_PAPER_ENFORCE", True),
        f"AGENT_FACT_GUARD_PAPER_ENFORCE={getattr(s, 'AGENT_FACT_GUARD_PAPER_ENFORCE', None)}",
    )
    rep.check(
        "Persistence 1-tick",
        int(getattr(s, "MIDLONG_PERSISTENCE_TICKS", 99) or 99) <= 1,
        f"MIDLONG_PERSISTENCE_TICKS={getattr(s, 'MIDLONG_PERSISTENCE_TICKS', None)}",
    )
    try:
        from backend.services.decision_core.pipeline import evaluate_midlong_open
        rep.check("evaluate_midlong_open 可导入", callable(evaluate_midlong_open))
    except Exception as exc:
        rep.check("evaluate_midlong_open 可导入", False, str(exc))
    try:
        from backend.services.decision_core.monte_carlo_gate import estimate_tail_risk
        mc = estimate_tail_risk(market_data={"atr_pct": 2.0}, sl_pct=0.04, side="buy", paths=100)
        rep.check("MonteCarlo 模块可用", mc.size_multiplier > 0, mc.detail[:80])
    except Exception as exc:
        rep.check("MonteCarlo 模块可用", False, str(exc))
    if getattr(s, "MIDLONG_THESIS_LEDGER_ENABLED", False):
        rep.warn(
            "MLTO thesis 面板",
            "MIDLONG_THESIS_LEDGER_ENABLED=true（thesis 更新可用，与 exec=false 并存）",
        )
    rep.check(
        "严格数据门控开启",
        bool(getattr(s, "STRICT_DATA_GATE", False)),
        f"STRICT_DATA_GATE={getattr(s, 'STRICT_DATA_GATE', None)}",
    )


def live_checks(rep: Reporter, symbol: str) -> None:
    from backend.services.unified_data_pool import unified_data_pool
    from backend.services.multi_timeframe_orchestrator import mt_orchestrator

    sym = symbol.upper()
    print(f"\n--- live snapshot: {sym} ---")
    snapshot = unified_data_pool.capture_snapshot([sym])

    kline_tfs = sorted({tf for s, tf in getattr(snapshot, "klines", {}).keys() if str(s).upper() == sym})
    required_tfs = ["1h", "4h", "1d", "1w"]
    rep.check(
        "K线周期齐全",
        all(tf in kline_tfs for tf in required_tfs),
        f"periods={kline_tfs}",
    )

    indicators = (getattr(snapshot, "indicators", {}) or {}).get(sym, {})
    ok_mid, detail_mid = _has_all(
        indicators,
        ["rsi_4h", "macd_4h", "adx_4h", "ema_9_4h", "ema_21_4h", "atr_4h"],
    )
    rep.check("中期 4h 指标齐全", ok_mid, detail_mid)

    ok_mid_context, detail_mid_context = _has_all(
        indicators,
        ["rsi", "macd", "adx_1d", "ema_9_1d", "ema_21_1d"],
    )
    rep.check("中期 1h择时 + 1d背景齐全", ok_mid_context, detail_mid_context)

    ok_long, detail_long = _has_all(
        indicators,
        ["adx_1d", "ema_9_1d", "adx_1w", "rsi_1w", "macd_1w", "ema_9_1w"],
    )
    rep.check("长期 1d + 1w 指标齐全", ok_long, detail_long)

    planning = getattr(snapshot, "per_symbol_planning", {}) or {}
    rep.check(
        "LongTermPlanner per-symbol 规划写入",
        sym in planning,
        f"keys={list(planning.keys())}",
    )

    decision = mt_orchestrator.evaluate(sym, snapshot)
    mid_details = getattr(decision.mid_view, "details", "") or ""
    long_details = getattr(decision.long_view, "details", "") or ""

    rep.check(
        "编排器中期采用 4h 动量",
        "(4h)" in mid_details and "4h" in mid_details,
        mid_details[:180],
    )
    rep.check(
        "编排器中期纳入 1d 背景",
        "1d" in mid_details,
        mid_details[:180],
    )
    rep.check(
        "编排器长期纳入 1w 周线",
        "1w" in long_details,
        long_details[:180],
    )

    slots = getattr(decision, "recommended_slots", []) or []
    actions = getattr(decision, "slot_actions", {}) or {}
    rep.check(
        "槽位推荐字段可用",
        isinstance(slots, list) and isinstance(actions, dict),
        f"slots={slots}, actions={actions}",
    )

    print("\n--- decision summary ---")
    print(
        f"L={decision.long_view.bias}({decision.long_view.confidence:.0%}) "
        f"M={decision.mid_view.bias}({decision.mid_view.confidence:.0%}) "
        f"S={decision.short_view.bias}({decision.short_view.confidence:.0%}) "
        f"slots={slots}"
    )
    print(f"MID:  {mid_details[:260]}")
    print(f"LONG: {long_details[:260]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify three-cycle strategy data and orchestration.")
    parser.add_argument("--symbol", default="BTC", help="Symbol to verify, default BTC")
    parser.add_argument("--no-live", action="store_true", help="Only run static checks; skip market snapshot")
    args = parser.parse_args()

    rep = Reporter()
    print("=== verify_three_cycle_strategy ===\n")

    try:
        static_checks(rep)
    except Exception as exc:
        rep.check("静态检查异常", False, str(exc))

    if not args.no_live:
        try:
            live_checks(rep, args.symbol)
        except Exception as exc:
            rep.check("实盘快照检查异常", False, str(exc))

    print("\n=== result ===")
    print(f"PASS={len(rep.results) - rep.fail_count} FAIL={rep.fail_count}")
    return 1 if rep.fail_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
