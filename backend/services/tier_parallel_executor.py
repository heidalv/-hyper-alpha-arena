"""
多周期并行执行器 — 短/中/长三个 tier 独立分析 + 跨 tier 协调

架构:
  ┌─────────────────────────────────────────────────┐
  │            TierParallelExecutor                  │
  │                                                  │
  │  ┌──────────┐ ┌──────────┐ ┌──────────┐        │
  │  │ Short    │ │ Mid      │ │ Long     │        │
  │  │ Pipeline │ │ Pipeline │ │ Pipeline │        │
  │  │ (15m)    │ │ (1h/4h)  │ │ (4h/1d)  │        │
  │  └────┬─────┘ └────┬─────┘ └────┬─────┘        │
  │       │            │            │               │
  │       └────────────┼────────────┘               │
  │                    ▼                             │
  │          CrossTierCoordinator                    │
  │          (冲突解决 + 曝险聚合)                    │
  │                    │                             │
  │                    ▼                             │
  │          Coordinated Decisions                   │
  └─────────────────────────────────────────────────┘

每个 Pipeline 独立:
1. 收集 tier 专属 K 线数据
2. 过滤出属于该 tier 的持仓和策略
3. 调用 analyst_system (含 LLM) 做该 tier 的决策
4. 预算限制在 tier 分配额度内

跨 tier 协调:
- 方向冲突检测 (short=buy, long=sell → 降低双方置信度)
- 聚合曝险限制 (所有 tier 保证金总和 ≤ 总权益 * 安全比例)
- 多数投票: 当 2/3 tier 方向一致时增强信号
"""
import logging
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FTimeoutError
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# tier 常量
TIERS = ("short", "mid", "long")
TIER_LABELS = {"short": "短线", "mid": "中线", "long": "长线"}

# 单 tier 最大等待时间（秒）：LLM 分析 + 数据拉取
# 即使某个 tier 卡死，也不会拖垮调度器
_TIER_TIMEOUT_SEC = 240


@dataclass
class SessionSnapshot:
    """FullAutoSession 的线程安全快照：只读的纯 Python 对象，可跨线程传递。

    主线程从 ORM session 对象读取所需字段后拷贝到此对象，worker 线程只访问纯值，
    避免 SQLAlchemy session/identity-map 的线程安全问题。
    """
    status: str = "running"
    symbols: list = field(default_factory=list)
    current_drawdown: float = 0.0
    max_drawdown: float = 0.0
    max_total_drawdown_pct: float = 0.30
    total_pnl: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    account_id: int = 0
    session_id: str = ""


@dataclass
class TierAnalysisResult:
    """单个 tier 的分析结果"""
    tier: str
    decisions: List[Dict] = field(default_factory=list)
    reports: Dict[str, Any] = field(default_factory=dict)
    overall_assessment: str = ""
    risk_level: str = "medium"
    elapsed_ms: float = 0.0
    error: str = ""
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class CoordinatedResult:
    """跨 tier 协调后的最终结果"""
    all_decisions: List[Dict] = field(default_factory=list)
    tier_results: Dict[str, TierAnalysisResult] = field(default_factory=dict)
    conflicts: List[Dict] = field(default_factory=list)
    coordination_notes: List[str] = field(default_factory=list)
    tier_budgets: Dict[str, float] = field(default_factory=dict)


class TierParallelExecutor:
    """多周期并行执行器"""

    def __init__(self):
        self._last_tier_analysis_ts: Dict[str, float] = {}
        self._thread_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="tier")

    def execute_parallel_tiers(
        self,
        db: Session,
        session,
        active_ids: list,
        market_summary: dict,
        account_id: int,
    ) -> Optional[CoordinatedResult]:
        """主入口：并行执行三个 tier 的分析，然后协调。

        Returns:
            CoordinatedResult 或 None（全部 tier 都跳过/失败时）
        """
        from backend.config.settings import (
            TIER_ANALYSIS_INTERVAL, TIER_BUDGET_ALLOCATION,
            TIER_PRIMARY_PERIOD, TIER_MAX_MARGIN_PCT,
        )
        from backend.database.models import AIStrategy as _AIStrategy

        mode = session.status
        now = time.time()

        strats_by_tier: Dict[str, List] = {"short": [], "mid": [], "long": []}
        if active_ids:
            all_strats = db.query(_AIStrategy).filter(
                _AIStrategy.strategy_id.in_(list(active_ids)),
                _AIStrategy.status.in_(["active", "paused"]),
            ).all()
            for s in all_strats:
                tier = getattr(s, "timeframe_tier", None) or "mid"
                genome = s.genome or {}
                nature = genome.get("trade_nature", "")
                if nature:
                    from backend.services.sub_position_manager import NATURE_TO_TIER
                    tier = NATURE_TO_TIER.get(nature, tier)
                strats_by_tier.setdefault(tier, []).append(s)

        balance_info = self._get_balance(db, account_id)
        balance_info["account_id"] = account_id
        total_equity = float(balance_info.get("total_equity", 10000))
        tier_budgets = {
            t: total_equity * TIER_BUDGET_ALLOCATION.get(t, 0.3)
            for t in TIERS
        }

        positions_list = self._get_positions(db, account_id)

        try:
            from backend.services.hold_timeout_review_queue import sync_open_positions
            _ht_n = sync_open_positions(account_id, positions_list)
            if _ht_n:
                logger.info(
                    f"[TierExecutor] 持仓时限复审队列: account={account_id} "
                    f"登记/更新 {_ht_n} 个"
                )
        except Exception as _ht_err:
            logger.debug(f"[TierExecutor] 持仓时限队列同步跳过: {_ht_err}")

        positions_by_tier: Dict[str, List[Dict]] = {"short": [], "mid": [], "long": []}
        for p in positions_list:
            p_tier = p.get("timeframe_tier") or "mid"
            p_nature = p.get("trade_nature", "")
            if p_nature:
                from backend.services.sub_position_manager import NATURE_TO_TIER
                p_tier = NATURE_TO_TIER.get(p_nature, p_tier)
            positions_by_tier.setdefault(p_tier, []).append(p)

        tier_results: Dict[str, TierAnalysisResult] = {}
        tiers_to_run = []

        for tier in TIERS:
            interval = TIER_ANALYSIS_INTERVAL.get(tier, 600)
            last_ts = self._last_tier_analysis_ts.get(tier, 0)
            if (now - last_ts) < interval and strats_by_tier.get(tier):
                tier_results[tier] = TierAnalysisResult(
                    tier=tier, skipped=True,
                    skip_reason=f"冷却中({int(now - last_ts)}/{interval}s)")
                continue

            # P5-fix(2026-05-08): 该 tier 即便没有 active 策略，但只要有 open 持仓
            # 就必须运行 AI 决策 — 不然持仓"无人看护"，AI 不会出 hold/reduce/close 的管理决策
            _tier_has_positions = bool(positions_by_tier.get(tier))
            if not strats_by_tier.get(tier) and not _tier_has_positions:
                tier_results[tier] = TierAnalysisResult(
                    tier=tier, skipped=True,
                    skip_reason="无活跃策略且无持仓")
                continue

            tiers_to_run.append(tier)

        if not tiers_to_run:
            logger.info("[TierExecutor] 所有 tier 均跳过（冷却或无策略）")
            return None

        tier_brief = {
            t: {
                "strategies": len(strats_by_tier.get(t, [])),
                "positions": len(positions_by_tier.get(t, [])),
                "budget": round(tier_budgets[t], 2),
            }
            for t in tiers_to_run
        }
        logger.info(
            f"[TierExecutor] 🚀 启动多周期并行分析: tiers={tiers_to_run}, "
            f"total_equity={total_equity:.2f}, 明细={tier_brief}"
        )

        t_start_all = time.time()

        market_shared_cache = self._build_shared_market_cache(
            symbols=list({
                s.primary_symbol
                for tier in tiers_to_run
                for s in strats_by_tier.get(tier, [])
                if s.primary_symbol
            }),
            session_symbols=session.symbols or [],
        )

        # 把 ORM session 对象快照成纯字典，避免跨线程访问 SQLAlchemy session 对象
        session_snapshot = self._snapshot_session(session)

        tier_results.update(self._execute_tiers_in_parallel(
            tiers_to_run=tiers_to_run,
            session_snapshot=session_snapshot,
            account_id=account_id,
            strats_by_tier=strats_by_tier,
            positions_by_tier=positions_by_tier,
            positions_list=positions_list,
            market_summary=market_summary,
            balance_info=balance_info,
            tier_budgets=tier_budgets,
            mode=mode,
            market_shared_cache=market_shared_cache,
        ))

        for tier in tiers_to_run:
            self._last_tier_analysis_ts[tier] = now

        coordinated = self._coordinate_across_tiers(
            tier_results, market_summary, tier_budgets)

        total_decisions = len(coordinated.all_decisions)
        active_tiers = [t for t, r in tier_results.items() if not r.skipped and not r.error]
        total_elapsed_ms = (time.time() - t_start_all) * 1000
        logger.info(
            f"[TierExecutor] ✅ 并行执行完成: 活跃tier={active_tiers}, "
            f"总决策={total_decisions}, 冲突={len(coordinated.conflicts)}, "
            f"总耗时={total_elapsed_ms:.0f}ms"
        )

        return coordinated

    # ══════════════════════════════════════════════════
    #  真并行执行 (ThreadPoolExecutor + 独立 DB Session)
    # ══════════════════════════════════════════════════

    def _execute_tiers_in_parallel(
        self,
        tiers_to_run: List[str],
        session_snapshot: "SessionSnapshot",
        account_id: int,
        strats_by_tier: Dict[str, List],
        positions_by_tier: Dict[str, List[Dict]],
        positions_list: List[Dict],
        market_summary: dict,
        balance_info: dict,
        tier_budgets: Dict[str, float],
        mode: str,
        market_shared_cache: dict,
    ) -> Dict[str, TierAnalysisResult]:
        """真正的并行执行：每个 tier 用独立线程 + 独立 DB session + 超时保护。"""
        tier_results: Dict[str, TierAnalysisResult] = {}

        # 为避免和主 db 竞争，每个 tier 线程用独立 SessionLocal
        def _worker(tier: str) -> TierAnalysisResult:
            thread_id = threading.get_ident()
            t0 = time.time()
            logger.info(
                f"[TierExecutor] ▶ {TIER_LABELS[tier]}({tier}) 启动 "
                f"tid={thread_id} 策略数={len(strats_by_tier.get(tier, []))} "
                f"持仓数={len(positions_by_tier.get(tier, []))}"
            )
            from backend.database.connection import SessionLocal
            tier_db = SessionLocal()
            try:
                result = self._run_single_tier(
                    db=tier_db,
                    session=session_snapshot,
                    tier=tier,
                    strategies=strats_by_tier.get(tier, []),
                    positions=positions_by_tier.get(tier, []),
                    all_positions=positions_list,
                    market_summary=market_summary,
                    balance_info=balance_info,
                    tier_budget=tier_budgets[tier],
                    mode=mode,
                    market_shared_cache=market_shared_cache,
                )
                result.elapsed_ms = (time.time() - t0) * 1000
                logger.info(
                    f"[TierExecutor] ✔ {TIER_LABELS[tier]}({tier}) 完成 "
                    f"决策={len(result.decisions)} 耗时={result.elapsed_ms:.0f}ms"
                )
                return result
            except Exception as e:
                logger.error(
                    f"[TierExecutor] ✗ {TIER_LABELS[tier]}({tier}) 异常: {e}",
                    exc_info=True,
                )
                return TierAnalysisResult(
                    tier=tier,
                    error=str(e),
                    elapsed_ms=(time.time() - t0) * 1000,
                )
            finally:
                try:
                    tier_db.close()
                except Exception:
                    pass

        futures = {
            self._thread_pool.submit(_worker, tier): tier
            for tier in tiers_to_run
        }

        deadline = time.time() + _TIER_TIMEOUT_SEC + 30
        try:
            for future in as_completed(futures, timeout=_TIER_TIMEOUT_SEC + 60):
                tier = futures[future]
                try:
                    remaining = max(1.0, deadline - time.time())
                    tier_results[tier] = future.result(timeout=remaining)
                except FTimeoutError:
                    logger.error(
                        f"[TierExecutor] ⏱ {TIER_LABELS[tier]}({tier}) "
                        f"超时({_TIER_TIMEOUT_SEC}s)，标记为 error"
                    )
                    future.cancel()
                    tier_results[tier] = TierAnalysisResult(
                        tier=tier,
                        error=f"tier 超时({_TIER_TIMEOUT_SEC}s)",
                        elapsed_ms=_TIER_TIMEOUT_SEC * 1000,
                    )
                except Exception as e:
                    logger.error(
                        f"[TierExecutor] ✗ {TIER_LABELS[tier]}({tier}) future 异常: {e}",
                        exc_info=True,
                    )
                    tier_results[tier] = TierAnalysisResult(
                        tier=tier, error=str(e),
                    )
        except FTimeoutError:
            # as_completed 自身超时：仍有 future 未完成
            logger.error(
                f"[TierExecutor] ⏱ as_completed 整体超时({_TIER_TIMEOUT_SEC + 60}s)，"
                f"尚有 future 未完成"
            )
        finally:
            # 安全收尾：尝试取消所有未完成的 future
            for future, tier in list(futures.items()):
                if not future.done():
                    logger.warning(
                        f"[TierExecutor] ⚠ 取消未完成的 {TIER_LABELS[tier]}({tier}) future"
                    )
                    try:
                        future.cancel()
                    except Exception:
                        pass
                    if tier not in tier_results:
                        tier_results[tier] = TierAnalysisResult(
                            tier=tier,
                            error=f"tier 超时未完成",
                            elapsed_ms=_TIER_TIMEOUT_SEC * 1000,
                        )

        return tier_results

    @staticmethod
    def _snapshot_session(session) -> SessionSnapshot:
        """从 ORM session 对象创建线程安全的快照。"""
        return SessionSnapshot(
            status=getattr(session, "status", "running") or "running",
            symbols=list(getattr(session, "symbols", []) or []),
            current_drawdown=float(getattr(session, "current_drawdown", 0) or 0),
            max_drawdown=float(getattr(session, "max_drawdown", 0) or 0),
            max_total_drawdown_pct=float(
                getattr(session, "max_total_drawdown_pct", 0.30) or 0.30
            ),
            total_pnl=float(getattr(session, "total_pnl", 0) or 0),
            total_trades=int(getattr(session, "total_trades", 0) or 0),
            winning_trades=int(getattr(session, "winning_trades", 0) or 0),
            account_id=int(
                getattr(session, "paper_account_id", None)
                or getattr(session, "account_id", 0)
                or 0
            ),
            session_id=str(getattr(session, "session_id", "") or ""),
        )

    def _build_shared_market_cache(
        self, symbols: List[str], session_symbols: List[str]
    ) -> dict:
        """预构建跨 tier 共享的 K 线缓存（在主线程中做，避免每个 tier 重复拉取）。

        返回结构: {(symbol, period): klines_list}
        """
        from backend.config.settings import TIER_PRIMARY_PERIOD, TIER_KLINE_PERIODS
        cache: Dict[tuple, list] = {}

        unique_symbols = list(dict.fromkeys(
            [*(symbols or []), *(session_symbols or [])]
        ))
        periods_needed: set = set()
        for _tier, _periods in TIER_KLINE_PERIODS.items():
            periods_needed.update(_periods)
        for tier in TIERS:
            periods_needed.add(TIER_PRIMARY_PERIOD.get(tier, "1h"))

        if not unique_symbols or not periods_needed:
            return cache

        try:
            from backend.services.market_data import get_kline_data
            count_map = {"5m": 80, "15m": 60, "1h": 100, "4h": 150, "1d": 200}
            t0 = time.time()
            fetched = 0
            for sym in unique_symbols:
                for period in periods_needed:
                    try:
                        count = count_map.get(period, 100)
                        klines = get_kline_data(sym, period=period, count=count)
                        if klines:
                            cache[(sym, period)] = klines
                            fetched += 1
                    except Exception as e:
                        logger.debug(
                            f"[TierExecutor] 共享 K 线缓存 {sym}@{period} 失败: {e}"
                        )
            logger.info(
                f"[TierExecutor] 📦 共享 K 线缓存就绪: "
                f"{fetched}/{len(unique_symbols)*len(periods_needed)} 条, "
                f"耗时={(time.time()-t0)*1000:.0f}ms"
            )
        except Exception as e:
            logger.warning(f"[TierExecutor] 构建共享缓存失败: {e}")

        return cache

    def _run_single_tier(
        self,
        db: Session,
        session,
        tier: str,
        strategies: list,
        positions: List[Dict],
        all_positions: List[Dict],
        market_summary: dict,
        balance_info: dict,
        tier_budget: float,
        mode: str,
        market_shared_cache: Optional[dict] = None,
    ) -> TierAnalysisResult:
        """运行单个 tier 的完整分析管道。"""
        from backend.services.trading_analysts import analyst_system
        from backend.config.settings import TIER_PRIMARY_PERIOD, TIER_KLINE_PERIODS
        from backend.database.models import StrategyMemory as _SM

        result = TierAnalysisResult(tier=tier)
        primary_period = TIER_PRIMARY_PERIOD.get(tier, "1h")

        self._enrich_positions_with_strategy_meta(db, positions, strategies)

        tier_market_summary = self._build_tier_market_summary(
            tier, market_summary, primary_period,
            session.symbols or [],
            market_shared_cache=market_shared_cache,
        )

        intel_data = {}
        for sym, info in (tier_market_summary or {}).items():
            if isinstance(info, dict):
                intel_data[sym] = {
                    "sentiment_index": info.get("sentiment_index", 50),
                    "sentiment_zone": info.get("sentiment_zone", "neutral"),
                    "whale_direction": info.get("whale_direction", 0),
                    "whale_summary": info.get("whale_summary", ""),
                    "derivatives_signal": info.get("derivatives_signal", "neutral"),
                    "derivatives_interpretation": info.get("derivatives_interpretation", ""),
                    "funding_rate": info.get("funding_rate", 0),
                    "oi_total": info.get("oi_total", 0),
                    "oi_change_1h": info.get("oi_change_1h", 0),
                    "liquidation_1h_long": info.get("liquidation_1h_long", 0),
                    "liquidation_1h_short": info.get("liquidation_1h_short", 0),
                    "long_short_ratio": info.get("long_short_ratio", 1.0),
                    "news_top_event": info.get("news_top_event", ""),
                    "news_impact": info.get("news_impact", 0),
                    "intelligence_prompt": info.get("intelligence_prompt", ""),
                    "market_cycle": info.get("market_cycle", ""),
                    "cycle_confidence": info.get("cycle_confidence", 0),
                    "position_bias": info.get("position_bias", ""),
                    "data_reliable": info.get("data_reliable", True),
                }

        session_stats = {
            "current_drawdown": getattr(session, "current_drawdown", 0) or 0,
            "max_drawdown": session.max_drawdown or 0,
            "max_total_drawdown_pct": session.max_total_drawdown_pct or 0.30,
            "total_pnl": session.total_pnl or 0,
            "win_rate": (round((session.winning_trades or 0) / (session.total_trades or 1), 3)
                         if (session.total_trades or 0) > 0 else 0),
            "total_trades": session.total_trades or 0,
            "winning_trades": session.winning_trades or 0,
        }

        strategies_info = []
        strat_ids = [s.strategy_id for s in strategies]
        _mems = {}
        try:
            _mem_rows = db.query(_SM).filter(_SM.strategy_id.in_(strat_ids)).all()
            _mems = {m.strategy_id: m for m in _mem_rows}
        except Exception:
            pass

        for strat in strategies:
            mem = _mems.get(strat.strategy_id)
            _total = (mem.total_trades or 0) if mem else 0
            _wr = round((mem.win_rate or 0) * 100, 1) if mem else 0.0
            _avg_profit = (mem.avg_profit or 0) if mem else 0
            _avg_loss = (mem.avg_loss or 0) if mem else 0
            strategies_info.append({
                "strategy_id": strat.strategy_id,
                "name": strat.name or "",
                "primary_symbol": strat.primary_symbol or "",
                "status": strat.status or "",
                "tier": tier,
                "trade_nature": (strat.genome or {}).get("trade_nature", "swing"),
                "total_trades": _total,
                "win_rate": _wr,
                "total_pnl": round(
                    _avg_profit * _total * (mem.win_rate or 0) +
                    _avg_loss * _total * (1 - (mem.win_rate or 0)), 2
                ) if mem and _total > 0 else 0,
                "avg_profit": _avg_profit,
                "avg_loss": _avg_loss,
                "max_drawdown": (mem.max_drawdown or 0) if mem else 0,
                "sharpe_ratio": (mem.sharpe_ratio or 0) if mem else 0,
            })

        # P5-fix(2026-05-08): tier_symbols 必须并入"该 tier 已有持仓的 symbol"，
        # 否则即便 _run_single_tier 被调用，AI 也不会评估这些有持仓的 symbol
        # （因为它们不在 tier_symbols 里 → master_controller 不会为它们生成决策）。
        tier_symbols_set = set(
            s.primary_symbol for s in strategies if s.primary_symbol
        )
        for _p in (positions or []):
            _psym = (_p.get("symbol") or "").upper()
            if _psym:
                tier_symbols_set.add(_psym)
        tier_symbols = list(tier_symbols_set)

        tier_balance = dict(balance_info)
        tier_balance["_tier"] = tier
        tier_balance["_tier_budget"] = tier_budget
        tier_balance["_tier_label"] = TIER_LABELS.get(tier, tier)

        tier_margin_used = sum(float(p.get("margin", 0)) for p in positions)
        tier_balance["_tier_margin_used"] = tier_margin_used
        tier_balance["_tier_margin_available"] = max(0, tier_budget - tier_margin_used)

        try:
            from backend.services.hold_timeout_review_queue import get_alerts_for_prompt
            _all_alerts = get_alerts_for_prompt(
                int(balance_info.get("account_id") or 0)
            )
            tier_balance["_hold_timeout_alerts"] = [
                a for a in _all_alerts if (a.get("tier") or "mid") == tier
            ]
        except Exception:
            tier_balance["_hold_timeout_alerts"] = []

        try:
            analysis_result = analyst_system.run_full_analysis(
                positions=positions,
                market_envs=tier_market_summary,
                intel_data=intel_data,
                balance=tier_balance,
                session_stats=session_stats,
                strategies=strategies_info,
                symbols=tier_symbols,
                mode=mode,
                db=db,
                account_id=int(balance_info.get("account_id") or 0) or None,
            )

            result.reports = analysis_result.get("reports", {})
            master = analysis_result.get("master_decision", {})
            result.overall_assessment = master.get("overall_assessment", "")
            result.risk_level = master.get("risk_level", "medium")
            decisions = master.get("decisions", [])

            # 后置过滤：AI 可能对市场上下文中无该 tier 策略的交易对生成 OPEN 决策，
            # 这些决策无法执行（策略查找时会匹配不到），在此层降级为 hold
            strategy_symbol_set = set(
                s.primary_symbol for s in strategies if s.primary_symbol
            )
            for dec in list(decisions):
                _dec_action = (dec.get("action") or "").lower()
                _dec_sym = (dec.get("symbol") or "").upper()
                if _dec_action in ("buy", "sell") and _dec_sym and _dec_sym not in strategy_symbol_set:
                    logger.info(
                        f"[TierExecutor|{tier}] 过滤: {_dec_sym} 有{_dec_action}决策"
                        f"但无{tier}策略，降级为hold"
                    )
                    dec["action"] = "hold"
                    dec["_tier_filtered"] = True
                    dec["_tier_filtered_reason"] = f"no_{tier}_strategy"

            from backend.services.sub_position_manager import normalize_nature, TIER_TO_NATURE
            _tier_nature = TIER_TO_NATURE.get(tier, "swing")
            for dec in decisions:
                dec["tier"] = tier
                dec["_source_tier"] = tier
                if not dec.get("trade_nature"):
                    dec["trade_nature"] = _tier_nature
                else:
                    dec["trade_nature"] = normalize_nature(dec["trade_nature"])

                # ════════════════════════════════════════════════════════════
                # P5-fix(2026-05-08) — 后置硬拦截：防止 AI 用"采纳模板"豁免置信度门槛
                # 触发场景：BTC short @ 25% 置信度 + 24% 模板 + 多空看多 → AI 仍开 sell
                # 这是"瞎开单"的核心病根，必须在此层兜底，不依赖 AI 自觉。
                # ════════════════════════════════════════════════════════════
                _action = (dec.get("action") or "").lower()
                if _action in ("buy", "sell"):
                    _conf = float(dec.get("confidence", 0) or 0)
                    _nature = (dec.get("trade_nature") or "").lower()
                    _sym = (dec.get("symbol") or "").upper()

                    # 门槛 1: 与编排器/regime 联动的动态门槛（替代固定 45/55）
                    try:
                        from backend.services.entry_confidence_gate import resolve_entry_gate_pct
                        _sym_mkt = (tier_market_summary or {}).get(_sym, {}) if isinstance(tier_market_summary, dict) else {}
                        _orch = _sym_mkt.get("orchestrator", {}) if isinstance(_sym_mkt, dict) else {}
                        _gate = float(resolve_entry_gate_pct(tier, _sym_mkt.get("regime", ""), _orch))
                    except Exception:
                        _gate = 45.0
                    # 门槛 2: scalp 性质 → 在动态门槛上 +8
                    if _nature == "scalp":
                        _gate = min(60.0, _gate + 8.0)

                    # 门槛 3: 检查该 symbol 在本 tier 是否有"非 hold"的策略库模板信号
                    _has_strong_template = False
                    _has_verified_template = False
                    _max_tpl_signal_conf = 0.0
                    _tpl_signals_for_sym = []
                    try:
                        _sym_market = (tier_market_summary or {}).get(_sym, {}) if isinstance(tier_market_summary, dict) else {}
                        _tpl_signals_for_sym = _sym_market.get("template_signals", []) if isinstance(_sym_market, dict) else []
                        for _ts in _tpl_signals_for_sym:
                            if not isinstance(_ts, dict):
                                continue
                            if _ts.get("tier") != tier:
                                continue
                            _ts_dir = (_ts.get("direction") or "").lower()
                            _ts_conf = float(_ts.get("signal_confidence") or 0)
                            _max_tpl_signal_conf = max(_max_tpl_signal_conf, _ts_conf)
                            _ts_verified = bool(_ts.get("verified")) or "champion" in [
                                str(t).lower() for t in (_ts.get("tags") or [])
                            ]
                            _strong_thresh = 40 if _ts_verified else 45
                            if _ts_dir == _action and _ts_conf >= _strong_thresh:
                                _has_strong_template = True
                                if _ts_verified:
                                    _has_verified_template = True
                    except Exception:
                        pass

                    # verified 强信号：降低越权放行门槛（软优先，非 bypass）
                    if _has_verified_template:
                        _gate = max(38.0, _gate - 7.0)

                    # 兜底拦截规则（任一命中 → 强制改为 hold）
                    _kill_reasons = []
                    if _conf < _gate:
                        _kill_reasons.append(
                            f"confidence={_conf:.0f} < 门槛{_gate:.0f}"
                            + ("(scalp高门槛)" if _nature == "scalp" else "")
                        )
                    # ── 混合模式: 预筛选通过豁免 ──
                    _prescreen_passed = bool(dec.get("_prescreen_passed", False))

                    # 弱模板 + 中低置信度 = 高度疑似瞎开（预筛选通过则豁免此条）
                    _weak_tpl_threshold = 60
                    if _has_verified_template:
                        _weak_tpl_threshold = 52
                    if (
                        not _has_strong_template
                        and _max_tpl_signal_conf < 30
                        and _conf < _weak_tpl_threshold
                        and not _prescreen_passed  # 预筛选通过 → 跳过此拦截
                    ):
                        _kill_reasons.append(
                            f"无强模板支持(最强模板信号={_max_tpl_signal_conf:.0f}<30) "
                            f"且 confidence={_conf:.0f} < 60"
                        )

                    # 预筛选通过但置信度仍偏低 → 降级警告而非硬拦截（阈值 60→50）
                    if (
                        not _has_strong_template
                        and _max_tpl_signal_conf < 30
                        and _prescreen_passed
                        and _conf < 50
                    ):
                        _kill_reasons.append(
                            f"[预筛选通过但] confidence={_conf:.0f} < 50 仍不足"
                        )

                    if _kill_reasons:
                        _orig = _action
                        dec["action"] = "hold"
                        dec["_p5_intercepted"] = True
                        dec["_p5_intercept_reason"] = " | ".join(_kill_reasons)
                        _existing_reason = dec.get("reasoning", "") or ""
                        dec["reasoning"] = (
                            f"[P5硬拦截] 原决策={_orig.upper()} 被强制改为 hold。"
                            f"理由：{dec['_p5_intercept_reason']}。"
                            f"原 reasoning（仅供审计）：{_existing_reason[:300]}"
                        )
                        logger.warning(
                            f"[TierExecutor|P5-Gate] 拦截 {_sym}/{tier} {_orig.upper()}→hold: "
                            f"{dec['_p5_intercept_reason']}"
                        )

                self._annotate_recall_source(dec, tier_market_summary, tier)

            result.decisions = decisions

        except Exception as e:
            result.error = str(e)
            logger.error(
                f"[TierExecutor] {TIER_LABELS[tier]} 分析师系统异常: {e}",
                exc_info=True)

        return result

    @staticmethod
    def _annotate_recall_source(dec: dict, tier_market_summary: dict, tier: str) -> None:
        """标记决策召回来源（verified 模板 / 策略记忆 / 默认 LLM）。"""
        reasoning = (dec.get("reasoning") or "").lower()
        action = (dec.get("action") or "").lower()
        sym = (dec.get("symbol") or "").upper()
        if dec.get("recall_source"):
            return

        if "采纳模板" in reasoning or "verified_template" in reasoning:
            dec["recall_source"] = "verified_template"
            return

        sym_market = (tier_market_summary or {}).get(sym, {}) if isinstance(tier_market_summary, dict) else {}
        tpl_signals = sym_market.get("template_signals") or []
        for ts in tpl_signals:
            if not isinstance(ts, dict) or ts.get("tier") != tier:
                continue
            if not ts.get("verified"):
                continue
            ts_dir = (ts.get("direction") or "").lower()
            ts_conf = float(ts.get("signal_confidence") or 0)
            if ts_dir == action and ts_conf >= 40:
                dec["recall_source"] = "verified_template"
                dec.setdefault("recall_template_id", ts.get("template_id"))
                return

        if "教训" in reasoning or "成功模式" in reasoning or "strategy_memory" in reasoning:
            dec["recall_source"] = "strategy_memory"
            return

        dec.setdefault("recall_source", "llm_default")

    def _build_tier_market_summary(
        self, tier: str, market_summary: dict,
        primary_period: str, symbols: list,
        market_shared_cache: Optional[dict] = None,
    ) -> dict:
        """构建 tier 专属的市场摘要，注入 tier 级 K 线数据。

        优先使用 market_shared_cache，否则按需拉取。
        """
        tier_summary = {}
        for sym in symbols:
            base_info = {}
            if isinstance(market_summary, dict) and sym in market_summary:
                src = market_summary[sym]
                if isinstance(src, dict):
                    base_info = dict(src)

            base_info["_analysis_tier"] = tier
            base_info["_analysis_period"] = primary_period

            kline_data = None
            if market_shared_cache:
                kline_data = market_shared_cache.get((sym, primary_period))
            if kline_data is None:
                kline_data = self._fetch_tier_klines(sym, tier, primary_period)
            if kline_data is not None:
                base_info["_tier_klines"] = kline_data
                base_info["_tier_kline_period"] = primary_period

            tier_summary[sym] = base_info

        return tier_summary

    def _fetch_tier_klines(
        self, symbol: str, tier: str, primary_period: str
    ) -> Optional[list]:
        """获取 tier 专属的 K 线数据（shared cache miss 时的兜底）。"""
        try:
            from backend.services.market_data import get_kline_data
            count_map = {"short": 60, "mid": 100, "long": 200}
            count = count_map.get(tier, 100)
            return get_kline_data(symbol, period=primary_period, count=count)
        except Exception as e:
            logger.debug(f"[TierExecutor] {symbol}/{tier} K线获取失败: {e}")
            return None

    def _enrich_positions_with_strategy_meta(
        self, db: Session, positions: List[Dict], strategies: list
    ):
        """为持仓注入策略元数据（tier, trade_nature）。"""
        strat_meta = {}
        for s in strategies:
            genome = s.genome or {}
            strat_meta[s.strategy_id] = {
                "timeframe_tier": getattr(s, "timeframe_tier", None) or "mid",
                "trade_nature": genome.get("trade_nature") or "swing",
            }
        for p in positions:
            sid = p.get("strategy_id")
            meta = strat_meta.get(sid, {}) if sid else {}
            p.setdefault("timeframe_tier", meta.get("timeframe_tier", "mid"))
            p.setdefault("trade_nature", meta.get("trade_nature", "swing"))

    def _get_balance(self, db: Session, account_id: int) -> dict:
        try:
            from backend.services.paper_trading_engine import paper_engine
            return paper_engine.get_balance(db, account_id) or {}
        except Exception:
            return {"total_equity": 10000, "available": 10000}

    def _get_positions(self, db: Session, account_id: int) -> List[Dict]:
        try:
            from backend.services.paper_trading_engine import paper_engine
            return paper_engine.get_positions(db, account_id) or []
        except Exception:
            return []

    # ══════════════════════════════════════════════════
    #  跨 tier 协调
    # ══════════════════════════════════════════════════

    def _coordinate_across_tiers(
        self,
        tier_results: Dict[str, TierAnalysisResult],
        market_summary: dict,
        tier_budgets: Dict[str, float],
    ) -> CoordinatedResult:
        """跨 tier 协调：检测冲突、调整置信度、聚合决策。"""
        from backend.config.settings import CROSS_TIER_CONFLICT_POLICY

        coordinated = CoordinatedResult(
            tier_results=tier_results,
            tier_budgets=tier_budgets,
        )

        all_decisions = []
        for tier, result in tier_results.items():
            if result.skipped or result.error:
                continue
            all_decisions.extend(result.decisions)

        symbol_directions: Dict[str, Dict[str, str]] = {}
        for dec in all_decisions:
            sym = (dec.get("symbol") or "").upper()
            tier = dec.get("_source_tier", "mid")
            action = str(dec.get("action", "hold")).lower()
            if action in ("buy", "sell") and sym:
                symbol_directions.setdefault(sym, {})[tier] = action

        for sym, tier_actions in symbol_directions.items():
            unique_actions = set(tier_actions.values())
            if len(unique_actions) <= 1:
                if len(tier_actions) >= 2:
                    # F1-3: 同方向tier合并去重（避免三重暴露）
                    merged_action = list(unique_actions)[0]
                    max_tier = max(tier_actions.keys(),
                                   key=lambda t: tier_budgets.get(t, 0))
                    merged_decisions = [d for d in all_decisions
                                        if d.get("symbol", "").upper() == sym
                                        and d.get("_source_tier") in tier_actions
                                        and str(d.get("action", "")).lower() == merged_action]
                    for i, dec in enumerate(sorted(merged_decisions,
                                                   key=lambda d: tier_budgets.get(d.get("_source_tier", "mid"), 0),
                                                   reverse=True)):
                        if i == 0:
                            dec["_merged_from_tiers"] = list(tier_actions.keys())
                            dec["_is_merged"] = True
                        else:
                            dec["action"] = "hold"
                            dec["_merged_into_master"] = True
                            dec["reasoning"] = (dec.get("reasoning", "") +
                                f" [tier合并: 已由{max_tier}tier执行]")
                    coordinated.coordination_notes.append(
                        f"{sym}: {len(tier_actions)}个tier同向({merged_action})→合并为{max_tier}tier执行")
                continue

            conflict = {
                "symbol": sym,
                "tier_actions": dict(tier_actions),
                "policy_applied": CROSS_TIER_CONFLICT_POLICY,
            }

            if CROSS_TIER_CONFLICT_POLICY == "conservative":
                for dec in all_decisions:
                    if (dec.get("symbol", "").upper() == sym
                            and dec.get("_source_tier") in tier_actions
                            and str(dec.get("action", "")).lower() in ("buy", "sell")):
                        raw_conf = float(dec.get("confidence", 50))
                        penalty = 20
                        dec["confidence"] = max(10, raw_conf - penalty)
                        dec["_conflict_penalty"] = penalty
                        dec["reasoning"] = (
                            dec.get("reasoning", "") +
                            f" [跨tier冲突: {tier_actions}→置信度-{penalty}]")
                conflict["resolution"] = "双方置信度降低20"

            elif CROSS_TIER_CONFLICT_POLICY == "aggressive":
                buy_count = sum(1 for a in tier_actions.values() if a == "buy")
                sell_count = sum(1 for a in tier_actions.values() if a == "sell")
                majority = "buy" if buy_count > sell_count else "sell"
                minority = "sell" if majority == "buy" else "buy"
                for dec in all_decisions:
                    if (dec.get("symbol", "").upper() == sym
                            and str(dec.get("action", "")).lower() == minority
                            and dec.get("_source_tier") in tier_actions):
                        dec["action"] = "hold"
                        dec["_conflict_overridden"] = True
                        dec["reasoning"] = (
                            dec.get("reasoning", "") +
                            f" [多数投票覆盖: {majority}胜出]")
                conflict["resolution"] = f"多数投票→{majority}"

            else:
                conflict["resolution"] = "独立运行(无干预)"

            coordinated.conflicts.append(conflict)
            coordinated.coordination_notes.append(
                f"{sym}: 方向冲突 {tier_actions} → {conflict['resolution']}")

        self._apply_aggregate_exposure_limits(all_decisions, tier_budgets)

        coordinated.all_decisions = all_decisions
        return coordinated

    def _apply_aggregate_exposure_limits(
        self, decisions: List[Dict], tier_budgets: Dict[str, float]
    ):
        """F2-3: 硬上限 — 超预算直接拒绝而非降仓，按置信度排序优先执行高置信度决策。"""
        from backend.config.settings import TIER_MAX_MARGIN_PCT

        tier_used: Dict[str, float] = {"short": 0.0, "mid": 0.0, "long": 0.0}
        active_decisions = [
            d for d in decisions
            if str(d.get("action", "hold")).lower() in ("buy", "sell")
        ]
        # 按置信度降序：高置信度优先分配预算
        for dec in sorted(
            active_decisions,
            key=lambda d: float(d.get("confidence", 0)),
            reverse=True,
        ):
            tier = dec.get("_source_tier", "mid")
            budget = tier_budgets.get(tier, 0.25)
            position_pct = float(dec.get("target_portion_of_balance", 0.05))

            used = tier_used.get(tier, 0.0)
            if used + position_pct > budget:
                # F2-3: 超预算直接拒绝
                dec["action"] = "hold"
                dec["_budget_blocked"] = True
                dec["reasoning"] = (dec.get("reasoning", "") +
                    f" [tier预算已用尽: {used:.1%}+{position_pct:.1%}>{budget:.0%}]")
                logger.info(
                    f"[TierExecutor] {dec.get('symbol', '?')}/{tier} "
                    f"预算拒绝: {used:.1%}+{position_pct:.1%}>{budget:.0%}"
                )
            else:
                tier_used[tier] = used + position_pct
                dec["_tier_budget"] = budget
                dec["_tier_used"] = tier_used[tier]
                dec["_tier_max_margin_pct"] = TIER_MAX_MARGIN_PCT.get(tier, 0.4)
                dec["_budget_blocked"] = False


tier_executor = TierParallelExecutor()
