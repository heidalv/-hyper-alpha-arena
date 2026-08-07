"""
Parity Score 验证管线 — compute_parity_score / run_parity_score_pipeline（P3，规划文档§4.5）。

背景（已核实）：该模块此前完全不存在（v2.0文档中同名声称的模块经核查也不存在），
本文件从零实现。用于验证第3.1节"因子进化闭环打通"的实际效果——如果回测/实盘持续
背离，说明晋升门槛（DSR/PBO/影子期）没有真正挡住过拟合策略，或者实盘执行层本身有
bug（比如成交价异常偏离市场）。

公式（文档§4.5，权重固定）：
    Score = 1 - Σ_i w_i * |M_live_i - M_bt_i| / max(|M_bt_i|, ε)
六个维度 i：成交均价偏差(0.25)、滑点偏差(0.25)、胜率偏差(0.15)、盈亏比偏差(0.15)、
最大回撤偏差(0.10)、Sharpe偏差(0.10)。注意 i 是"6个指标维度"而不是"逐笔交易"
（0.25+0.25+0.15+0.15+0.10+0.10=1.0，恰好是6个维度的权重，不是N笔交易的权重），
所以本实现在【指标层面】比较实盘 vs 回测，而不是强行逐笔配对（独立跑的实盘 scalp
循环和离线回放天然不会产生逐笔一一对应的成交序列，逐笔配对既不可行也不是文档原意）。

数据来源与已知局限（诚实标注，不夸大）：
    - 实盘侧：`paper_orders` 表里 pnl 不为空的"平仓单"（真实成交，非模拟）。
    - 回测侧：用 `LivePipelineBacktestEngine`（与 AI 主编排链完全同代码同参数的离线
      回放引擎）在同一 symbol + 同一时间窗口上重放。这是"同一套决策管线"级别的一致
      性验证，但不是 scalp_factor_router 独立短线循环的逐笔复现（后者依赖大量实时
      订单流/清算数据，历史无法完全重建）——scalp/intraday nature 的 Parity Score
      因此置信度低于 swing/trend_follow/position，本模块在报告里会标注 tier，供人工
      判断权重。
    - "成交均价偏差"："滑点偏差"：实盘侧用成交价相对最近K线收盘价（公允参考价）的
      偏离度衡量；回测侧假设按bar收盘价精确成交（0偏差），滑点用回测引擎里建模的
      固定常数 SLIPPAGE 代替（backtest_evolution_engine.SLIPPAGE）。

每周自动：取 7 天实盘成交 → 回放同 symbol/tier 回测 → 计算 Parity Score →
Score<0.85 告警，Score<0.70 且**连续 FREEZE_CONSECUTIVE_REQUIRED(2) 周**都命中才通过
RuntimeGovernor 提交 disabled_natures 意图（冻结该 nature 新开仓，已有仓位不受影响）。
加连续性要求的原因（2026-07-18 首次实跑发现）：avg_fill_price_dev/avg_slippage 两个维度
(权重合计0.5)的回测侧基准是引擎固定常数，与实盘侧"真实K线内价格噪声"不是同一量纲，
单周就可能把这两维度打满导致 score=0.000，即便 win_rate/profit_factor/sharpe 等真实
可比指标显示 live 其实优于 bt。要求连续两周复现，避免单次结构性噪声就误杀整条 nature。
"""
from __future__ import annotations

import bisect
import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

WEIGHTS: Dict[str, float] = {
    "avg_fill_price_dev": 0.25,
    "avg_slippage": 0.25,
    "win_rate": 0.15,
    "profit_factor": 0.15,
    "max_drawdown": 0.10,
    "sharpe": 0.10,
}
ALERT_THRESHOLD = 0.85
FREEZE_THRESHOLD = 0.70
# [2026-07-20 修复] avg_fill_price_dev/avg_slippage 的 bt 基准是回测引擎的固定小常数
# （SLIPPAGE≈0.03%），实盘只要有一点真实成交噪音，相对偏差就必然打满 _DEV_CAP——这两个
# 维度合计权重0.5，等于"只要有实盘噪音，score 就几乎永远≈0"，与噪音是否真的异常无关。
# 实测（2026-07-18首次实跑）：scalp/swing 均命中 score=0.000 且连续2周触发冻结，但两者的
# win_rate/profit_factor/sharpe/max_drawdown（真正可比、不受该常数缺陷影响的4个维度）
# 显示 live 其实与 bt 相当甚至更优——是指标构造缺陷导致的系统性误杀，不是真实执行问题
# （详见上方 docstring 与 _apply_freeze 注释）。原"连续2周"安全阀设计意图是防单次噪声，
# 但这是结构性 bug 而非随机噪声，每周都会稳定复现，安全阀形同虚设，长期会把所有 nature
# 逐个冻死。修复：冻结动作额外要求"核心可比指标"(下方 CORE_FREEZE_METRICS)自身也跌破
# 阈值才生效；score 本身与告警口径不变，保证仪表盘/历史趋势不受影响，只收紧冻结判据。
CORE_FREEZE_METRICS = {"win_rate", "profit_factor", "max_drawdown", "sharpe"}
# 核心指标的"更优"方向：win_rate/profit_factor/sharpe 越高越好；max_drawdown 是跌幅
# 幅度，越低越好。冻结判据只应惩罚"live 比 bt 差"的方向——原 dev 公式是纯幅度差
# (|lv-bv|/bv)，不分方向，导致 live 明明表现更好(如 profit_factor live 0.91 > bt 0.50)
# 也被算出高偏差、拖累分数，这是本次实测 07-18 冻结里比"常数基准"更隐蔽的第二个误杀
# 因子，一并修复（仅影响下方 core_score 的冻结判据，不影响原 score/告警口径）。
_CORE_HIGHER_IS_BETTER = {"win_rate": True, "profit_factor": True, "sharpe": True, "max_drawdown": False}
MIN_LIVE_TRADES = 5
DEFAULT_LOOKBACK_DAYS = 7
_EPS = 1e-6
_DEV_CAP = 5.0  # 单指标偏差截断上限，避免bt某指标恰好≈0时把总分直接打穿到很负的极端值
# LivePipelineBacktestEngine 每隔几根bar会跑一次1147个因子的全量计算(factor_signal_weight>0时)，
# 单symbol几百根K线就要数十秒。实盘一个nature往往横跨几十个symbol，全量回放对"每周一次"的
# 定时任务不现实，因此只取实盘成交笔数最多的头部symbol做回放——这些symbol的PnL贡献本来就占
# 大头，足够代表该nature的整体一致性，是"周期性自动任务"和"逐笔精确复现"之间的务实取舍。
MAX_SYMBOLS_PER_NATURE = 5

NATURE_TIER_MAP: Dict[str, str] = {
    "scalp": "short", "intraday": "short",
    "swing": "mid",
    "trend_follow": "long", "position": "long",
}

REPORT_DIR = os.path.join("data", "parity_reports")
HISTORY_PATH = os.path.join("data", "parity_score_history.jsonl")
FREEZE_STATE_PATH = os.path.join("data", "parity_score_freeze_state.json")
# [2026-07-18 加固] avg_fill_price_dev/avg_slippage 的回测侧基准是引擎里的固定常数 SLIPPAGE
# （见docstring），而实盘侧是"成交价相对最近K线收盘价的真实偏离"——两者衡量的根本不是同一
# 件事：回测按设计就是0噪声(精确按bar价成交)，实盘天然有真实的bar内价格波动，几乎必然让这两个
# 维度(权重合计0.5，占总分一半)相对偏差远超100%。首次实跑验证（2026-07-18）就实测到scalp/swing
# 两个nature的这两项单独就能把score打成0.000，而它们的win_rate/profit_factor/sharpe等"同口径
# 真实指标"其实live还优于bt——说明是指标本身的构造性噪声，不是真的live执行出了问题。
# 直接改WEIGHTS会偏离规划文档§4.5写死的权重公式，因此不动分数公式本身，而是在"是否真正执行
# 冻结"这个动作上加一层连续性校验：要求连续 FREEZE_CONSECUTIVE_REQUIRED 次(默认2周)都触发
# 冻结门槛才真正提交disabled_natures——分数/告警仍按文档公式如实上报，只是防止单次噪声就
# 一刀切停掉整条nature的实盘开仓。
FREEZE_CONSECUTIVE_REQUIRED = 2


def _cfg(name: str, default: Any) -> Any:
    from backend.config import settings as _s
    return getattr(_s, name, default)


@dataclass
class ParityMetric:
    name: str
    weight: float
    live_value: float
    bt_value: float
    deviation: float


@dataclass
class ParityScoreResult:
    nature: str
    tier: str
    lookback_days: int
    computed_at: str
    n_live_trades: int
    n_bt_trades: int
    symbols: List[str] = field(default_factory=list)
    available: bool = False
    score: float = 1.0
    metrics: List[ParityMetric] = field(default_factory=list)
    alert: bool = False
    frozen: bool = False
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nature": self.nature, "tier": self.tier, "lookback_days": self.lookback_days,
            "computed_at": self.computed_at, "n_live_trades": self.n_live_trades,
            "n_bt_trades": self.n_bt_trades, "symbols": self.symbols,
            "available": self.available, "score": round(self.score, 4),
            "metrics": [vars(m) for m in self.metrics],
            "alert": self.alert, "frozen": self.frozen, "reason": self.reason,
        }


def _tier_period(tier: str) -> str:
    from backend.services.strategy_params_registry import TIER_CONFIG
    return TIER_CONFIG.get(tier, TIER_CONFIG["mid"]).get("default_timeframe", "1h")


def _as_utc_ts(dt: Optional[datetime]) -> Optional[int]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _fetch_live_closing_orders(nature: str, lookback_days: int) -> List[Dict[str, Any]]:
    """实盘侧样本：paper_orders 里 pnl 不为空(=平仓成交)的真实订单。"""
    from backend.database.connection import SessionLocal
    from backend.database.models import PaperOrder

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    db = SessionLocal()
    try:
        rows = (
            db.query(PaperOrder)
            .filter(
                PaperOrder.trade_nature == nature,
                PaperOrder.status == "filled",
                PaperOrder.pnl.isnot(None),
                PaperOrder.filled_at.isnot(None),
                PaperOrder.filled_at >= cutoff,
            )
            .order_by(PaperOrder.filled_at.asc())
            .all()
        )
        out = []
        for r in rows:
            qty = float(r.filled_quantity or r.quantity or 0)
            entry = float(r.entry_price or 0)
            if qty <= 0 or entry <= 0:
                continue
            out.append({
                "symbol": r.symbol, "side": (r.side or "").lower(),
                "quantity": qty, "entry_price": entry,
                "filled_price": float(r.filled_price or 0),
                "leverage": float(r.leverage or 1.0),
                "pnl": float(r.pnl or 0),
                "filled_at": r.filled_at,
            })
        return out
    finally:
        db.close()


def _pnl_pct(order: Dict[str, Any]) -> float:
    """与 backtest_evolution_engine 完全相同的口径：pnl / margin（margin=quantity*entry/leverage）。"""
    margin = order["quantity"] * order["entry_price"] / max(order["leverage"], 1e-6)
    return order["pnl"] / margin if margin > 0 else 0.0


def _load_reference_bars(symbol: str, exchange: str, period: str, start_ts: int, end_ts: int,
                          warmup_bars: int = 250) -> list:
    """加载覆盖[start_ts,end_ts]的K线，前置warmup_bars根供EMA200等指标预热用（回测侧需要，
    实盘侧参考价查找也复用同一批数据，多出来的预热K线不影响 bisect 查找结果）。"""
    from backend.database.models import CryptoKline
    from backend.database.connection import MarketSessionLocal
    from backend.services.backtest_evolution_engine import Bar

    bar_seconds = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}.get(period, 3600)
    warm_start = start_ts - warmup_bars * bar_seconds

    db = MarketSessionLocal()
    try:
        rows = (
            db.query(CryptoKline)
            .filter(
                CryptoKline.symbol == symbol, CryptoKline.period == period,
                CryptoKline.exchange == exchange,
                CryptoKline.timestamp >= warm_start, CryptoKline.timestamp <= end_ts,
            )
            .order_by(CryptoKline.timestamp.asc())
            .all()
        )
    finally:
        db.close()

    bars = []
    for idx, r in enumerate(rows):
        bars.append(Bar(
            timestamp=int(r.timestamp), dt_str=r.datetime_str or "",
            o=float(r.open_price or 0), h=float(r.high_price or 0),
            l=float(r.low_price or 0), c=float(r.close_price or 0),
            v=float(r.volume or 0), idx=idx,
        ))
    return bars


def _nearest_close(bars: list, ts: Optional[int]) -> Optional[float]:
    """二分查找 <=ts 的最近一根K线收盘价，作为该时刻"公允参考价"。"""
    if not bars or ts is None:
        return None
    timestamps = [b.timestamp for b in bars]
    i = bisect.bisect_right(timestamps, ts) - 1
    if i < 0:
        i = 0
    return bars[i].c


def _returns_stats(pnl_pcts: List[float], span_days: float) -> Dict[str, float]:
    """从逐笔 pnl_pct 序列算 win_rate/profit_factor/max_drawdown/sharpe，
    live/bt 用完全相同的公式（与 backtest_evolution_engine._calculate_metrics 对齐），
    确保比较有意义。净值曲线用累计复利(1+pnl_pct)归一化，消除仓位规模不可比问题。"""
    if not pnl_pcts:
        return {"win_rate": 0.0, "profit_factor": 0.0, "max_drawdown": 0.0, "sharpe": 0.0}

    wins = [p for p in pnl_pcts if p > 0]
    losses = [p for p in pnl_pcts if p <= 0]
    win_rate = len(wins) / len(pnl_pcts)
    total_profit = sum(wins) if wins else 0.0
    total_loss = abs(sum(losses)) if losses else 0.001
    profit_factor = total_profit / total_loss if total_loss > 0 else 0.0

    equity = [1.0]
    for p in pnl_pcts:
        equity.append(equity[-1] * (1.0 + p))
    eq = np.array(equity)
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / np.where(peak > 0, peak, 1)
    max_drawdown = float(np.max(dd)) if len(dd) else 0.0

    sharpe = 0.0
    arr = np.array(pnl_pcts, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if len(arr) >= 2:
        std = float(np.std(arr, ddof=1))
        mean = float(np.mean(arr))
        if std > 1e-10:
            trades_per_year = len(arr) / max(span_days, 0.5) * 365.0
            sharpe = mean / std * math.sqrt(max(trades_per_year, 1))

    return {
        "win_rate": win_rate, "profit_factor": profit_factor,
        "max_drawdown": max_drawdown, "sharpe": sharpe,
    }


def _compute_live_side(orders: List[Dict[str, Any]], bars_by_symbol: Dict[str, list]) -> Dict[str, Any]:
    pnl_pcts = [_pnl_pct(o) for o in orders]
    price_devs: List[float] = []
    signed_slippage: List[float] = []
    for o in orders:
        ts = _as_utc_ts(o["filled_at"])
        ref = _nearest_close(bars_by_symbol.get(o["symbol"], []), ts)
        if ref and ref > 0 and o["filled_price"] > 0:
            price_devs.append(abs(o["filled_price"] - ref) / ref)
            sign = 1.0 if o["side"] in ("buy", "long") else -1.0
            signed_slippage.append(sign * (o["filled_price"] - ref) / ref)

    first_ts, last_ts = _as_utc_ts(orders[0]["filled_at"]), _as_utc_ts(orders[-1]["filled_at"])
    span_days = max((last_ts - first_ts) / 86400.0, 0.5) if first_ts and last_ts else 7.0

    stats = _returns_stats(pnl_pcts, span_days)
    stats["avg_fill_price_dev"] = float(np.mean(price_devs)) if price_devs else 0.0
    stats["avg_slippage"] = float(np.mean(signed_slippage)) if signed_slippage else 0.0
    stats["n_price_samples"] = len(price_devs)
    return stats


def _run_backtest_side(
    symbols: List[str], tier: str, start_ts: int, end_ts: int,
    bars_by_symbol: Optional[Dict[str, list]] = None,
) -> Dict[str, Any]:
    """用与AI主编排链完全同代码同参数的 LivePipelineBacktestEngine 重放同一时间窗口。

    `symbols` 应已由调用方限制为头部N个（见 MAX_SYMBOLS_PER_NATURE），因为该引擎
    在 factor_signal_weight>0 时每隔几根bar就要跑一次1147个因子的全量计算，
    全symbol无上限回放对"每周定时任务"这个使用场景不现实。
    """
    from backend.services.live_pipeline_backtest_engine import LivePipelineBacktestEngine
    from backend.services.strategy_params_registry import DEFAULT_PIPELINE_PARAMS
    from backend.services.backtest_evolution_engine import SLIPPAGE

    period = _tier_period(tier)
    all_pnl_pcts: List[float] = []
    used_symbols: List[str] = []

    for sym in symbols:
        bars = (bars_by_symbol or {}).get(sym) or _load_reference_bars(
            sym, "hyperliquid", period, start_ts, end_ts, warmup_bars=250,
        )
        eval_bars = [b for b in bars if b.timestamp >= start_ts - 3600]
        if len(eval_bars) < 60:
            logger.debug(f"[ParityScore] {sym}@{period} K线不足({len(eval_bars)}根)，跳过回放")
            continue
        try:
            engine = LivePipelineBacktestEngine(initial_capital=10000)
            result = engine.run(bars, DEFAULT_PIPELINE_PARAMS, tier=tier)
        except Exception as e:
            logger.warning(f"[ParityScore] {sym} 回测回放异常: {e}")
            continue
        if result and not result.error and result.total_trades > 0:
            used_symbols.append(sym)
            all_pnl_pcts.extend([t.pnl_pct for t in result.trades if math.isfinite(t.pnl_pct)])

    if not all_pnl_pcts:
        return {}

    span_days = max((end_ts - start_ts) / 86400.0, 0.5)
    stats = _returns_stats(all_pnl_pcts, span_days)
    # LivePipelineBacktestEngine 的开仓价 = bar.c * (1 ± SLIPPAGE)（见该文件"entry ="行），
    # 即回测本身也建模了一个确定性的成交价偏离常数，并非"完美0偏差"——用这个真实值做基准，
    # 而不是硬编码0（硬编码0会让该指标在有任何实盘执行噪音时永远打满截断上限，指标失去意义）。
    stats["avg_fill_price_dev"] = float(SLIPPAGE)
    stats["avg_slippage"] = float(SLIPPAGE)
    stats["n_trades"] = len(all_pnl_pcts)
    stats["symbols"] = used_symbols
    return stats


def _record_freeze_breach(nature: str, breached: bool) -> int:
    """记录该 nature 是否本轮触发了 FREEZE_THRESHOLD，返回当前连续触发次数（未触发则清零并返回0）。"""
    state: Dict[str, int] = {}
    try:
        if os.path.exists(FREEZE_STATE_PATH):
            with open(FREEZE_STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f) or {}
    except Exception:
        state = {}

    if breached:
        state[nature] = int(state.get(nature, 0)) + 1
    else:
        state[nature] = 0

    try:
        os.makedirs(os.path.dirname(FREEZE_STATE_PATH) or ".", exist_ok=True)
        with open(FREEZE_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[ParityScore] 冻结连续计数持久化失败: {e}")

    return state[nature]


def _apply_freeze(nature: str, score: float) -> bool:
    """提交 disabled_natures 冻结意图。与当前生效值取并集而不是直接覆盖——否则同一轮
    pipeline 里连续处理多个 nature 时，后一个 submit_intent 会把前一个 nature 挤掉
    （runtime_governor 按 (source,key) 存单条意图，同 source 再提交同 key 即替换）。"""
    try:
        from backend.services.runtime_governor import runtime_governor as gov
        from backend.services.runtime_tuning_store import get_tuning

        current = get_tuning("disabled_natures", []) or []
        if not isinstance(current, list):
            current = []
        merged = sorted(set(str(n).lower() for n in current) | {nature})

        gov.submit_intent(
            "disabled_natures", merged, source="parity_score", confidence=0.95,
            reason=f"parity_score={score:.3f}<{FREEZE_THRESHOLD}(回测/实盘一致性差，疑似过拟合或执行层异常)",
        )
        logger.warning(
            f"[ParityScore] {nature} Parity Score={score:.3f} < {FREEZE_THRESHOLD} → "
            f"已提交disabled_natures意图冻结新开仓(已有仓位不受影响)"
        )
        return True
    except Exception as e:
        logger.error(f"[ParityScore] {nature} 冻结意图提交失败: {e}")
        return False


def _send_alert(nature: str, score: float, metrics: List[ParityMetric], freeze: bool) -> None:
    try:
        import asyncio
        from backend.services.openclaw_notify import notify_system_event, NotifyLevel

        level = NotifyLevel.CRITICAL if freeze else NotifyLevel.WARNING
        title = "🧊 Parity Score冻结" if freeze else "⚠️ Parity Score告警"
        detail = ""
        if metrics:
            worst = max(metrics, key=lambda m: m.weight * m.deviation)
            detail = f"，主要偏差来源: {worst.name}(live={worst.live_value} bt={worst.bt_value})"
        msg = f"{title}: nature={nature} score={score:.3f}{detail}"
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(notify_system_event(msg, level=level))
        except RuntimeError:
            asyncio.run(notify_system_event(msg, level=level))
    except Exception as e:
        logger.debug(f"[ParityScore] 告警发送跳过: {e}")


def compute_parity_score(nature: str, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> ParityScoreResult:
    """对单个 trade_nature 计算 Parity Score。样本不足或回测无法回放时安全降级为 available=False。"""
    nature = (nature or "").lower()
    tier = NATURE_TIER_MAP.get(nature, "mid")
    computed_at = datetime.now(timezone.utc).isoformat()

    orders = _fetch_live_closing_orders(nature, lookback_days)
    if len(orders) < MIN_LIVE_TRADES:
        return ParityScoreResult(
            nature=nature, tier=tier, lookback_days=lookback_days, computed_at=computed_at,
            n_live_trades=len(orders), n_bt_trades=0, available=False,
            reason=f"实盘近{lookback_days}天{nature}平仓成交仅{len(orders)}笔(<{MIN_LIVE_TRADES})，样本不足跳过",
        )

    symbol_counts: Dict[str, int] = {}
    for o in orders:
        symbol_counts[o["symbol"]] = symbol_counts.get(o["symbol"], 0) + 1
    all_symbols = sorted(symbol_counts.keys())
    top_symbols = sorted(symbol_counts, key=symbol_counts.get, reverse=True)[:MAX_SYMBOLS_PER_NATURE]
    # 只对头部symbol跑回测回放（见 MAX_SYMBOLS_PER_NATURE 注释），但实盘侧指标仍用全部symbol的
    # 全部成交计算，不因为回放范围收窄而丢真实数据。
    orders_for_bt_universe = [o for o in orders if o["symbol"] in top_symbols]
    start_ts = _as_utc_ts(orders_for_bt_universe[0]["filled_at"]) if orders_for_bt_universe else _as_utc_ts(orders[0]["filled_at"])
    end_ts = _as_utc_ts(orders[-1]["filled_at"])
    period = _tier_period(tier)
    symbols = all_symbols

    bars_by_symbol = {
        sym: _load_reference_bars(sym, "hyperliquid", period, start_ts, end_ts, warmup_bars=250)
        for sym in symbols
    }

    live_stats = _compute_live_side(orders, bars_by_symbol)
    bt_stats = _run_backtest_side(top_symbols, tier, start_ts, end_ts, bars_by_symbol=bars_by_symbol)

    if not bt_stats:
        return ParityScoreResult(
            nature=nature, tier=tier, lookback_days=lookback_days, computed_at=computed_at,
            n_live_trades=len(orders), n_bt_trades=0, symbols=symbols, available=False,
            reason="回测回放未产出任何交易(K线数据不足或该窗口管线未触发信号)，无法比对",
        )

    metrics: List[ParityMetric] = []
    weighted_dev_sum = 0.0
    core_weighted_dev_sum = 0.0
    core_weight_total = sum(w for k, w in WEIGHTS.items() if k in CORE_FREEZE_METRICS) or 1.0
    for key, weight in WEIGHTS.items():
        lv = float(live_stats.get(key, 0.0))
        bv = float(bt_stats.get(key, 0.0))
        dev = min(abs(lv - bv) / max(abs(bv), _EPS), _DEV_CAP)
        weighted_dev_sum += weight * dev
        if key in CORE_FREEZE_METRICS:
            higher_better = _CORE_HIGHER_IS_BETTER.get(key, True)
            live_underperforms = (lv < bv) if higher_better else (lv > bv)
            core_weighted_dev_sum += weight * dev if live_underperforms else 0.0
        metrics.append(ParityMetric(
            name=key, weight=weight, live_value=round(lv, 6), bt_value=round(bv, 6), deviation=round(dev, 4),
        ))

    score = max(0.0, 1.0 - weighted_dev_sum)
    # 核心可比指标单独归一化重算的分数——不受 avg_fill_price_dev/avg_slippage 结构性
    # 缺陷影响，专用于冻结判据（见上方 CORE_FREEZE_METRICS 注释）。
    core_score = max(0.0, 1.0 - core_weighted_dev_sum / core_weight_total)
    alert = score < ALERT_THRESHOLD
    frozen = False
    reason_bits = [f"score={score:.3f} core_score={core_score:.3f}"]

    # 冻结判据：全量 score 跌破阈值，且核心可比指标(win_rate/profit_factor/max_drawdown/
    # sharpe)也确认跌破——避免 avg_fill_price_dev/avg_slippage 的常数基准缺陷单独打穿
    # score 就误杀整条 nature。alert/告警口径仍用全量 score，不受此收紧影响。
    genuine_breach = score < FREEZE_THRESHOLD and core_score < FREEZE_THRESHOLD
    if genuine_breach:
        breach_count = _record_freeze_breach(nature, breached=True)
        if breach_count >= FREEZE_CONSECUTIVE_REQUIRED:
            frozen = _apply_freeze(nature, score)
            reason_bits.append(
                f"<{FREEZE_THRESHOLD}(核心指标同样跌破)且连续{breach_count}次→冻结{nature}新开仓"
                + ("(已生效)" if frozen else "(提交失败)")
            )
        else:
            reason_bits.append(
                f"<{FREEZE_THRESHOLD}(核心指标同样跌破，第{breach_count}次，"
                f"连续{FREEZE_CONSECUTIVE_REQUIRED}次才冻结，观察中)"
            )
        _send_alert(nature, score, metrics, freeze=frozen)
    elif score < FREEZE_THRESHOLD:
        # score 跌破但核心可比指标正常：判定为 avg_fill_price_dev/avg_slippage 常数基准
        # 缺陷造成的误报，不计入冻结连续计数（清零，避免历史误报残留凑够连续次数）。
        _record_freeze_breach(nature, breached=False)
        reason_bits.append(
            f"<{FREEZE_THRESHOLD}但核心可比指标(core_score={core_score:.3f})未跌破→"
            "判定为执行噪声基准缺陷误报，不冻结"
        )
        if alert:
            _send_alert(nature, score, metrics, freeze=False)
    elif alert:
        _record_freeze_breach(nature, breached=False)
        reason_bits.append(f"<{ALERT_THRESHOLD}→告警")
        _send_alert(nature, score, metrics, freeze=False)
    else:
        _record_freeze_breach(nature, breached=False)

    return ParityScoreResult(
        nature=nature, tier=tier, lookback_days=lookback_days, computed_at=computed_at,
        n_live_trades=len(orders), n_bt_trades=int(bt_stats.get("n_trades", 0)),
        symbols=symbols, available=True, score=score, metrics=metrics,
        alert=alert, frozen=frozen, reason="; ".join(reason_bits),
    )


def run_parity_score_pipeline(
    natures: Optional[List[str]] = None, lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> Dict[str, Dict[str, Any]]:
    """每周调度入口：对所有(或指定) nature 计算 Parity Score，持久化报告+历史趋势记录。

    覆盖全量nature时（natures=None，默认行为），本轮结束后会用"这一轮实际算出的
    冻结集合"整体覆盖写入 disabled_natures（而不是每个nature单独调用_apply_freeze
    时的"与当前值取并集"）——否则某个nature这周分数回升、不再需要冻结，也会因为
    别的nature还在触发续期而永远躺在disabled_natures里出不去。
    """
    if not bool(_cfg("PARITY_SCORE_ENABLED", True)):
        logger.info("[ParityScore] PARITY_SCORE_ENABLED=false，跳过本轮")
        return {}

    full_sweep = natures is None
    natures = natures or list(NATURE_TIER_MAP.keys())
    results: Dict[str, Dict[str, Any]] = {}
    try:
        os.makedirs(REPORT_DIR, exist_ok=True)
    except Exception:
        pass

    for nature in natures:
        try:
            r = compute_parity_score(nature, lookback_days)
        except Exception as e:
            logger.error(f"[ParityScore] {nature} 计算异常: {e}", exc_info=True)
            continue

        results[nature] = r.to_dict()
        logger.info(
            f"[ParityScore] nature={nature} available={r.available} "
            f"score={r.score:.3f} live_trades={r.n_live_trades} bt_trades={r.n_bt_trades} reason={r.reason}"
        )
        try:
            with open(HISTORY_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"[ParityScore] 历史记录写入失败: {e}")

    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    report_path = os.path.join(REPORT_DIR, f"parity_report_{date_str}.json")
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[ParityScore] 报告写入失败: {e}")

    if full_sweep and results:
        try:
            _sync_full_frozen_set(natures, results)
        except Exception as e:
            logger.warning(f"[ParityScore] disabled_natures 全量同步失败: {e}")

    return results


def _sync_full_frozen_set(checked_natures: List[str], results: Dict[str, Dict[str, Any]]) -> None:
    """全量扫描结束后，用本轮真实算出的冻结集合覆盖写入 disabled_natures：
    - 本轮判定 frozen 的 nature -> 保留/加入
    - 本轮参与检查但已回升、不再frozen的 nature -> 移出
    - 本轮未参与检查的 nature（比如外部/手动加的）-> 原样保留，不动
    """
    from backend.services.runtime_governor import runtime_governor as gov
    from backend.services.runtime_tuning_store import get_tuning

    current = get_tuning("disabled_natures", []) or []
    if not isinstance(current, list):
        current = []
    current_set = {str(n).lower() for n in current}
    checked_set = {str(n).lower() for n in checked_natures}

    frozen_this_round = {
        n for n in checked_natures if results.get(n, {}).get("frozen")
    }
    recovered = checked_set & current_set - frozen_this_round

    new_set = (current_set - checked_set) | frozen_this_round
    if new_set == current_set:
        return

    merged = sorted(new_set)
    gov.submit_intent(
        "disabled_natures", merged, source="parity_score", confidence=0.95,
        reason=(
            f"Parity Score全量同步：冻结={sorted(frozen_this_round) or '无'} "
            f"解冻={sorted(recovered) or '无'}"
        ),
    )
    logger.info(f"[ParityScore] disabled_natures 全量同步 -> {merged} (解冻: {sorted(recovered)})")


def load_parity_history(nature: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    """读取历史趋势记录（供前端可视化），按 computed_at 倒序，最多 limit 条。"""
    if not os.path.exists(HISTORY_PATH):
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if nature and rec.get("nature") != nature:
                    continue
                out.append(rec)
    except Exception as e:
        logger.warning(f"[ParityScore] 历史记录读取失败: {e}")
        return []
    out.sort(key=lambda r: r.get("computed_at", ""), reverse=True)
    return out[:limit]
