"""
极端行情模拟 — ExtremeScenarioSimulator（P1，规划文档 §4.3 + §3.4 验收）。

背景（已核实）：
    - 规划文档 v2.0 提到的 `ExtremeScenarioSimulator` 此前完全不存在，本文件从零搭建。
    - 本地 `crypto_klines` 表最早数据仅到 2025-06-09（Hyperliquid/Asterdex 上线时间
      本就晚于 2021/2022 年的经典极端行情事件），对 2021-05-19 全网闪崩 / 2022-05
      LUNA崩盘 / 2022-11 FTX崩盘 / 2024-08-05 日元套利平仓闪崩 的本地数据覆盖均为
      0 行——因此无法做"重放当年真实K线"意义上的历史重放。
    - 清算历史同样受限：Coinalyze 免费API仅能查最近4小时窗口，HyperliquidClient
      无清算历史接口。本地 symbol_aux_timeseries 自 2026-08-15 起已有
      kline_enrichment 落库序列（链上/社交，前向积累中）；liquidation_events
      自 2026-08-15 起按小时聚合落库（见 services/liquidation_collector.py）；
      raw_market_events 目前仍只有 kline 类型影子事件。

设计取舍（避免"用假数据自欺欺人的测试"）：
    1. 优先尝试 replay_historical_wicks() 扫描本地已有K线里真实存在的
       wick_ratio>3.0 事件（数据深度不足时天然返回少量甚至0个事件，如实反映
       当前数据覆盖现状，不掩盖问题）。
    2. 数据不足时用 synthesize_known_event_wicks() 按已知事件日历合成插针窗口
       ——但合成公式与生产环境 ScalpExecutionGate._check_wick_manipulation
       的 wick_ratio 公式逐字段同源（_compute_wick_ratio_series 直接复制该
       公式），保证"测试用例"与"生产判定标准"口径一致，而不是自造一套宽松/
       严格标准让测试"看起来"通过。
    3. 验收方式不重新发明判定逻辑，而是把构造好的极端行情窗口真实喂给生产环境
       正在运行的 ScalpExecutionGate.evaluate() 和 UnifiedExitStateMachine.submit()
       单例，断言其产出符合预期（block / 硬事实直通），这样"测试通过"等价于
       "生产代码在极端行情下确实按预期工作"，而不是测试了一套平行实现。
    4. 清算级联部分（P2阶段"价格级清算热图"数据源到位前）只能用 Coinalyze
       近窗数据做规则化滑点放大近似，接口先占位，数据源升级时替换实现即可，
       不改变调用方。

用法：
    python -m backend.services.backtest_engine.extreme_scenario --symbol BTC
    python -m backend.services.backtest_engine.extreme_scenario --symbol BTC,ETH,SOL

输出：
    data/extreme_scenario_report_{date}.json — 压力测试报告
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 已知加密货币极端行情事件日历（本地数据不覆盖，作为合成基准的"标签"）。
# 时间戳只用于报告标注，不代表真的拿到了当年K线。
KNOWN_EVENTS: List[Dict[str, str]] = [
    {"label": "2021-05-19 全网闪崩", "start": "2021-05-19 00:00:00", "end": "2021-05-20 00:00:00"},
    {"label": "2022-05 LUNA/UST崩盘", "start": "2022-05-09 00:00:00", "end": "2022-05-13 00:00:00"},
    {"label": "2022-11 FTX崩盘", "start": "2022-11-07 00:00:00", "end": "2022-11-11 00:00:00"},
    {"label": "2024-08-05 日元套利平仓闪崩", "start": "2024-08-05 00:00:00", "end": "2024-08-06 00:00:00"},
]

WICK_RATIO_THRESHOLD = 3.0
HIGH_WICK_DENSITY_THRESHOLD = 0.30  # 与 ScalpExecutionGate 默认阈值一致


@dataclass
class WickEvent:
    """单次插针事件（真实扫描出的，或合成注入的）。"""
    symbol: str
    period: str
    start_ts: int
    end_ts: int
    peak_wick_ratio: float
    high_wick_density: float
    source: str  # "historical_scan" | "synthetic_calendar"
    klines: pd.DataFrame = field(repr=False)
    label: str = ""


@dataclass
class CascadeEvent:
    """清算级联事件（规则化近似，非价格级热图——见模块顶部说明）。"""
    symbol: str
    liquidation_long_1h: float
    liquidation_short_1h: float
    severity: str
    slippage_multiplier: float
    source: str  # "coinalyze_recent" | "insufficient_data"


def _compute_wick_ratio_series(df: pd.DataFrame) -> pd.Series:
    """与 scalp_execution_gate.py::_check_wick_manipulation 逐字段同源的公式。

    两处公式必须保持一致——否则"造的插针"和生产判定标准不同源，测试通过与否
    就没有意义。如果未来 Gate 侧公式调整，这里也要同步改。
    """
    o = df["open"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    c = df["close"].astype(float)
    upper_wick = (h - pd.concat([o, c], axis=1).max(axis=1)).clip(lower=0)
    lower_wick = (pd.concat([o, c], axis=1).min(axis=1) - l).clip(lower=0)
    body = (c - o).abs()
    return pd.concat([upper_wick, lower_wick], axis=1).max(axis=1) / (body + 1e-10)


def _compute_high_wick_density(df: pd.DataFrame) -> float:
    if len(df) == 0:
        return 0.0
    ratio = _compute_wick_ratio_series(df)
    return float((ratio > WICK_RATIO_THRESHOLD).sum()) / len(df)


class ExtremeScenarioSimulator:
    """基于历史真实插针/清算事件（或与生产同源公式合成的事件）重放的压力测试模块。"""

    def __init__(self):
        from backend.services.scalp.scalp_execution_gate import scalp_execution_gate
        from backend.services.exit.unified_exit_state_machine import exit_state_machine
        self._gate = scalp_execution_gate
        self._exit_sm = exit_state_machine

    # ── 数据源1：真实历史扫描 ──
    def replay_historical_wicks(self, symbol: str, period: str = "1h", lookback_days: int = 180) -> List[WickEvent]:
        """扫描本地已有K线中真实的高插针密度窗口。

        本地库当前最早仅到2025-06-09，lookback_days 超出该范围时自然返回
        较少甚至0个事件——如实反映数据覆盖现状，不用假数据掩盖。
        """
        try:
            from backend.services.data_center import data_center
            df = data_center.get_klines_df(symbol, period, count=lookback_days * 24)
        except Exception as e:
            logger.warning(f"[ExtremeScenario] {symbol} 取数失败: {e}")
            return []
        if df is None or len(df) < 20:
            logger.info(f"[ExtremeScenario] {symbol} 本地历史数据不足({0 if df is None else len(df)}行)，"
                        f"跳过真实历史扫描（不代表系统有bug，是数据覆盖深度问题）")
            return []
        return self._scan_wick_windows(df, symbol, period, source="historical_scan")

    def _scan_wick_windows(self, df: pd.DataFrame, symbol: str, period: str, source: str) -> List[WickEvent]:
        events: List[WickEvent] = []
        window_size = 20
        for start in range(0, len(df) - window_size, window_size):
            window = df.iloc[start:start + window_size]
            density = _compute_high_wick_density(window)
            if density > HIGH_WICK_DENSITY_THRESHOLD:
                idx = window.index
                start_ts = int(idx[0].timestamp()) if hasattr(idx[0], "timestamp") else int(idx[0])
                end_ts = int(idx[-1].timestamp()) if hasattr(idx[-1], "timestamp") else int(idx[-1])
                events.append(WickEvent(
                    symbol=symbol, period=period, start_ts=start_ts, end_ts=end_ts,
                    peak_wick_ratio=float(_compute_wick_ratio_series(window).max()),
                    high_wick_density=density, source=source, klines=window.reset_index(drop=True),
                    label=f"{symbol}@{start_ts}(真实历史插针)",
                ))
        return events

    # ── 数据源2：已知事件日历 + 与生产同源公式合成的插针窗（数据缺口下的主力方案） ──
    def synthesize_known_event_wicks(self, symbol: str, base_price: float = 50000.0, seed: int = 42) -> List[WickEvent]:
        """按 KNOWN_EVENTS 日历，用与 ScalpExecutionGate 完全同源的公式合成插针K线窗。"""
        rng = np.random.default_rng(seed)
        events = []
        for ev in KNOWN_EVENTS:
            df = self._build_synthetic_wick_window(rng, base_price=base_price, n=20, wick_density=0.40)
            density = _compute_high_wick_density(df)
            events.append(WickEvent(
                symbol=symbol, period="synthetic_1h",
                start_ts=int(pd.Timestamp(ev["start"], tz="UTC").timestamp()),
                end_ts=int(pd.Timestamp(ev["end"], tz="UTC").timestamp()),
                peak_wick_ratio=float(_compute_wick_ratio_series(df).max()),
                high_wick_density=density, source="synthetic_calendar",
                klines=df, label=ev["label"],
            ))
        return events

    @staticmethod
    def _build_synthetic_wick_window(rng: np.random.Generator, base_price: float, n: int, wick_density: float) -> pd.DataFrame:
        """构造长影线K线窗口。wick_density 比例的K线是"上冲后回落"的插针形态，
        其余是正常波动——公式与 Gate 侧 wick_ratio 完全同源，见 _compute_wick_ratio_series。
        """
        n_wick = max(int(n * wick_density) + 1, 1)
        wick_idx = set(rng.choice(n, min(n_wick, n), replace=False).tolist())
        rows = []
        price = base_price
        for i in range(n):
            o = price
            if i in wick_idx:
                c = o * (1 + rng.uniform(-0.002, 0.002))
                h = o * (1 + rng.uniform(0.03, 0.08))
                l = min(o, c) * (1 - rng.uniform(0.001, 0.003))
            else:
                c = o * (1 + rng.uniform(-0.005, 0.005))
                h = max(o, c) * (1 + rng.uniform(0.001, 0.005))
                l = min(o, c) * (1 - rng.uniform(0.001, 0.005))
            rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 1000.0})
            price = c
        return pd.DataFrame(rows)

    # ── 数据源3：清算级联（规则化近似，见模块顶部限制说明） ──
    def replay_historical_liquidation_cascades(self, symbol: str) -> List[CascadeEvent]:
        try:
            from backend.services.derivatives_analytics_service import derivatives_analytics
            cluster = derivatives_analytics.get_liquidation_clusters(symbol, cached_only=False)
        except Exception as e:
            logger.debug(f"[ExtremeScenario] {symbol} 清算数据获取失败: {e}")
            return [CascadeEvent(symbol=symbol, liquidation_long_1h=0, liquidation_short_1h=0,
                                  severity="unknown", slippage_multiplier=1.0, source="insufficient_data")]
        if not cluster or cluster.get("total_1h", 0) < 100_000:
            return [CascadeEvent(symbol=symbol, liquidation_long_1h=cluster.get("liquidation_long_1h", 0) if cluster else 0,
                                  liquidation_short_1h=cluster.get("liquidation_short_1h", 0) if cluster else 0,
                                  severity="low", slippage_multiplier=1.0, source="coinalyze_recent")]
        severity = cluster["severity"]
        mult = {"low": 1.2, "medium": 1.6, "high": 2.5}.get(severity, 1.0)
        return [CascadeEvent(
            symbol=symbol,
            liquidation_long_1h=cluster["liquidation_long_1h"],
            liquidation_short_1h=cluster["liquidation_short_1h"],
            severity=severity, slippage_multiplier=mult, source="coinalyze_recent",
        )]

    # ── 验收核心：把事件喂给生产环境已落地的防护逻辑，而不是重新判定一遍 ──
    def verify_wick_protection(self, events: List[WickEvent]) -> Dict[str, Any]:
        """把插针窗口喂给生产环境 ScalpExecutionGate.evaluate()，验证是否正确 block。"""
        results = []
        for ev in events:
            try:
                fake_signal = SimpleNamespace(
                    action="buy", direction="long", factor_score=60,
                    entry_price=float(ev.klines["close"].iloc[-1]),
                )
                market_data = {"klines": ev.klines, "price": float(ev.klines["close"].iloc[-1])}
                decision = self._gate.evaluate(ev.symbol, fake_signal, market_data)
                blocked = (decision.tier == "block" and "high_wick_density" in (decision.reason or ""))
                results.append({
                    "label": ev.label or f"{ev.symbol}@{ev.start_ts}",
                    "source": ev.source,
                    "high_wick_density": round(ev.high_wick_density, 3),
                    "peak_wick_ratio": round(ev.peak_wick_ratio, 2),
                    "expected_block": True,
                    "actual_blocked": blocked,
                    "gate_tier": decision.tier,
                    "gate_reason": decision.reason,
                    "pass": blocked,
                })
            except Exception as e:
                logger.warning(f"[ExtremeScenario] {ev.label} 验证异常(记为失败): {e}")
                results.append({
                    "label": ev.label, "source": ev.source, "error": str(e),
                    "expected_block": True, "actual_blocked": False, "pass": False,
                })
        return {
            "total": len(results),
            "block_count": sum(1 for r in results if r.get("actual_blocked")),
            "missed_count": sum(1 for r in results if not r.get("actual_blocked")),
            "details": results,
        }

    def verify_exit_hard_fact(self, symbol: str, position_id: int = 999999) -> Dict[str, Any]:
        """验证插针刺穿SL后，UnifiedExitStateMachine 是否正确硬事实直通
        （不被 Layer2 保护层/最短持仓时间等拦截）。"""
        from backend.services.exit.exit_types import ExitRequest, ExitSource, ExitAction, PositionContext
        try:
            ctx = PositionContext(
                position_id=position_id, symbol=symbol, tier="short", side="long",
                entry_price=100.0, current_price=94.0, quantity=1.0,
                unrealized_pnl_pct=-6.0, hold_seconds=15,  # 极短持仓+亏损，若无硬事实直通，Layer2最短持仓保护会拦
            )
            req = ExitRequest(
                position_id=position_id, symbol=symbol, tier="short",
                source=ExitSource.STOP_LOSS.value, proposed_action=ExitAction.CLOSE.value,
                proposed_qty_ratio=1.0, reason_detail="extreme_scenario_stress_test_wick_pierce_sl",
            )
            decision = self._exit_sm.submit(req, ctx)
            passthrough = decision.action == ExitAction.CLOSE.value and decision.source == ExitSource.STOP_LOSS.value
            return {
                "expected_action": "close", "actual_action": decision.action,
                "hard_fact_passthrough": passthrough, "reason": decision.reason, "pass": passthrough,
            }
        except Exception as e:
            logger.warning(f"[ExtremeScenario] {symbol} ExitSM硬事实验证异常: {e}")
            return {"error": str(e), "pass": False}

    # ── 报告汇总 ──
    def run_full_stress_test(self, symbol: str = "BTC") -> Dict[str, Any]:
        real_events = self.replay_historical_wicks(symbol)
        synth_events = self.synthesize_known_event_wicks(symbol)
        all_events = real_events + synth_events

        wick_result = self.verify_wick_protection(all_events)
        exit_result = self.verify_exit_hard_fact(symbol)
        cascade_events = self.replay_historical_liquidation_cascades(symbol)

        report = {
            "symbol": symbol,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "data_coverage_note": (
                f"本地历史数据扫描到{len(real_events)}个真实插针窗口；"
                f"因本地crypto_klines最早仅到2025-06-09(不覆盖2021/2022经典事件)，"
                f"补充{len(synth_events)}个与生产公式同源的已知事件日历合成窗口"
            ),
            "wick_events_tested": wick_result["total"],
            "wick_block_count": wick_result["block_count"],
            "wick_block_rate": round(wick_result["block_count"] / max(wick_result["total"], 1), 4),
            "wick_missed_count": wick_result["missed_count"],
            "wick_details": wick_result["details"],
            "exit_hard_fact_passthrough": exit_result.get("pass", False),
            "exit_hard_fact_detail": exit_result,
            "cascade_events_tested": len(cascade_events),
            "cascade_severity": [c.severity for c in cascade_events],
            "cascade_slippage_multiplier": [c.slippage_multiplier for c in cascade_events],
            "cascade_data_limitation_note": (
                "Coinalyze免费API仅能查最近4小时清算窗口，HyperliquidClient无清算历史接口，"
                "本地清算/链上历史浅（liquidation_events 2026-08-15 起小时聚合前向积累，"
                "raw_market_events 仅有kline影子）——此项为近似规则化滑点"
                "放大系数，非真实价格级清算热图重放（数据源升级后替换实现）"
            ),
            "overall_pass": wick_result["missed_count"] == 0 and exit_result.get("pass", False),
        }
        return report


extreme_scenario_simulator = ExtremeScenarioSimulator()


def run_and_save(symbols: List[str]) -> Dict[str, Any]:
    reports = {sym: extreme_scenario_simulator.run_full_stress_test(sym) for sym in symbols}
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join("data", f"extreme_scenario_report_{date_str}.json")
    os.makedirs("data", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"generated_at": now.isoformat(), "symbols": symbols, "reports": reports}, f,
                   ensure_ascii=False, indent=2, default=str)
    logger.info(f"[ExtremeScenario] 报告已写入 {out_path}")
    for sym, r in reports.items():
        logger.info(
            f"[ExtremeScenario] {sym}: 插针测试{r['wick_events_tested']}例 "
            f"block={r['wick_block_count']} missed={r['wick_missed_count']} "
            f"exit硬事实直通={r['exit_hard_fact_passthrough']} overall_pass={r['overall_pass']}"
        )
    return {"report_path": out_path, "reports": reports}


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="极端行情模拟(历史插针/清算重放)压力测试")
    parser.add_argument("--symbol", type=str, default="BTC,ETH,SOL")
    args = parser.parse_args()
    symbols = [s.strip() for s in args.symbol.split(",") if s.strip()]
    result = run_and_save(symbols)
    print(json.dumps({k: v for k, v in result.items() if k != "reports"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
