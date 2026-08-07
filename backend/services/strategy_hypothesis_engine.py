"""
StrategyHypothesisEngine — LLM 驱动的策略假设生成器

完整流程:
1. 感知: 汇总当前 regime + 因子快照 + 近期策略表现
2. 推理: 调用 LLM 生成可验证的交易假设
3. 验证: 用 BacktestEngine 做快速 240 bar 回测
4. 进化: 达标假设 → strategy_evolver 正式进化

达标标准:
- Sharpe > 0.8
- Win Rate > 45%
- MaxDD < 15%

调度: 每 6 小时由 EvolutionScheduler 触发
"""

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Hypothesis:
    """一个可验证的交易假设"""
    hypothesis_id: str
    description: str
    symbol: str = "BTC"
    exchange: str = "hyperliquid"
    market_type: str = "perp"
    period: str = "1h"
    direction: str = "long"     # "long" | "short" | "neutral"
    entry_conditions: Dict[str, Any] = field(default_factory=dict)
    exit_conditions: Dict[str, Any] = field(default_factory=dict)
    risk_params: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.5
    regime_context: str = ""
    source: str = "llm"
    snapshot_id: str = ""
    data_source: str = ""
    data_quality: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


@dataclass
class ValidationResult:
    """回测验证结果"""
    hypothesis_id: str
    passed: bool = False
    sharpe: float = 0.0
    win_rate: float = 0.0
    max_drawdown_pct: float = 100.0
    total_trades: int = 0
    total_pnl: float = 0.0
    promoted_template_id: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    error: str = ""


class StrategyHypothesisEngine:
    """LLM 驱动的策略假设生成器"""

    # 达标阈值
    MIN_SHARPE = 0.8
    MIN_WIN_RATE = 0.45
    MAX_DRAWDOWN = 15.0

    # 生成配置
    HYPOTHESES_PER_RUN = 4
    BACKTEST_BARS = 240

    def __init__(self):
        self._generated: List[Hypothesis] = []
        self._validated: List[ValidationResult] = []
        self._promoted_count = 0

    # ── Market-data foundation adapter ───────────

    @staticmethod
    def _default_exchange() -> str:
        try:
            from backend.services.exchange_config import get_active_exchange
            return (get_active_exchange() or "asterdex").strip().lower()
        except Exception:
            return "asterdex"

    def _market_scope(self, market_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        ctx = market_context or {}
        return {
            "exchange": str(ctx.get("exchange") or self._default_exchange()).strip().lower(),
            "market_type": str(ctx.get("market_type") or "perp"),
            "period": str(ctx.get("period") or ctx.get("timeframe") or "1h"),
        }

    def _load_klines_from_foundation(
        self,
        *,
        exchange: str,
        symbol: str,
        period: str,
        limit: int,
    ) -> tuple[list[dict[str, Any]], Dict[str, Any]]:
        """从新数据基座的标准消费入口读取 K 线和质量信息。"""
        symbol = symbol.upper()
        meta: Dict[str, Any] = {
            "exchange": exchange,
            "symbol": symbol,
            "period": period,
            "data_source": "",
            "snapshot_id": "",
            "data_quality": {},
            "exchange_profile": {},
        }

        try:
            from backend.services.snapshot_reader import snapshot_reader
            snapshot = snapshot_reader.get_snapshot(max_age=180)
            if snapshot:
                meta["snapshot_id"] = snapshot.get("snapshot_id", "")
                meta["data_quality"] = snapshot.get("data_quality", {}) or {}
                meta["exchange_profile"] = snapshot.get("exchange_profiles", {}) or {}
                klines_by_key = snapshot.get("klines", {}) or {}
                keys = [
                    f"{exchange}:{symbol}:{period}",
                    f"active:{symbol}:{period}",
                    f"{symbol}:{period}",
                ]
                for key in keys:
                    rows = klines_by_key.get(key)
                    if rows and len(rows) >= min(limit, 50):
                        meta["data_source"] = "snapshot_reader"
                        return list(rows[-limit:]), meta
        except Exception as exc:
            meta["snapshot_error"] = f"{type(exc).__name__}: {exc}"

        try:
            from backend.services.kline_data_service import kline_service
            rows = kline_service.get_klines_from_db(
                symbol=symbol,
                period=period,
                count=limit,
                exchange=exchange,
            )
            if rows:
                meta["data_source"] = "kline_data_service"
                return list(rows[-limit:]), meta
        except Exception as exc:
            meta["kline_db_error"] = f"{type(exc).__name__}: {exc}"

        meta["data_source"] = "unavailable"
        return [], meta

    @staticmethod
    def _validation_payload(result: ValidationResult) -> Dict[str, Any]:
        return {
            "passed": result.passed,
            "sharpe": result.sharpe,
            "win_rate": result.win_rate,
            "max_drawdown_pct": result.max_drawdown_pct,
            "total_trades": result.total_trades,
            "total_pnl": result.total_pnl,
            "promoted_template_id": result.promoted_template_id,
            "details": result.details,
            "error": result.error,
        }

    def _persist_hypothesis(
        self,
        db,
        hypothesis: Hypothesis,
        status: str,
        validation: Optional[ValidationResult] = None,
        promoted_template_id: str = "",
        rejected_reason: str = "",
    ) -> None:
        if db is None:
            return
        try:
            from backend.database.models import StrategyHypothesis

            row = db.query(StrategyHypothesis).filter(
                StrategyHypothesis.hypothesis_id == hypothesis.hypothesis_id
            ).first()
            payload = {
                "status": status,
                "exchange": hypothesis.exchange,
                "market_type": hypothesis.market_type,
                "symbol": hypothesis.symbol,
                "period": hypothesis.period,
                "direction": hypothesis.direction,
                "confidence": hypothesis.confidence,
                "entry_conditions": hypothesis.entry_conditions,
                "exit_conditions": hypothesis.exit_conditions,
                "risk_params": hypothesis.risk_params,
                "source": hypothesis.source,
                "snapshot_id": hypothesis.snapshot_id,
                "data_source": hypothesis.data_source,
                "data_quality": hypothesis.data_quality,
                "validation_result": self._validation_payload(validation) if validation else None,
                "rejected_reason": rejected_reason,
                "promoted_template_id": promoted_template_id,
                "qaa_correlation_id": hypothesis.hypothesis_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            if row is None:
                row = StrategyHypothesis(
                    hypothesis_id=hypothesis.hypothesis_id,
                    name=hypothesis.description[:128],
                    description=hypothesis.description,
                    market_regime=hypothesis.regime_context[:32] if hypothesis.regime_context else None,
                    param_ranges=payload,
                    backtest_sharpe=validation.sharpe if validation else None,
                    promoted=status == "promoted",
                )
                db.add(row)
            else:
                existing = dict(row.param_ranges or {})
                existing.update(payload)
                row.name = hypothesis.description[:128]
                row.description = hypothesis.description
                row.market_regime = hypothesis.regime_context[:32] if hypothesis.regime_context else row.market_regime
                row.param_ranges = existing
                if validation:
                    row.backtest_sharpe = validation.sharpe
                row.promoted = status == "promoted" or bool(row.promoted)
            db.commit()

            try:
                from backend.services.qaa_evolution_bridge import qaa_bridge
                if qaa_bridge._enabled and qaa_bridge.history is not None:
                    qaa_bridge.history.record(
                        target_id=hypothesis.hypothesis_id,
                        action=f"hypothesis_{status}",
                        domain="trading",
                        details=payload,
                    )
            except Exception as qaa_err:
                logger.debug(f"[HypothesisEngine] QAA hypothesis audit skip: {qaa_err}")
        except Exception:
            db.rollback()
            raise

    # ── Public API ───────────────────────────────

    def generate_hypotheses(
        self,
        market_context: Dict[str, Any],
        symbols: Optional[List[str]] = None,
        db=None,
    ) -> List[Hypothesis]:
        """
        基于市场上下文, 调用 LLM 生成交易假设。

        Args:
            market_context: {regime, factor_snapshot, recent_performance, ...}
            symbols: 目标交易对列表

        Returns:
            假设列表
        """
        symbols = symbols or ["BTC"]
        scope = self._market_scope(market_context)
        hypotheses: List[Hypothesis] = []

        prompt = self._build_prompt(market_context, symbols)

        try:
            from backend.services.llm_config_service import get_llm_config, call_llm_api_sync

            llm_config = get_llm_config()
            if not llm_config:
                logger.warning("[HypothesisEngine] LLM 未配置, 使用规则回退")
                hypotheses = self._fallback_hypotheses(market_context, symbols)
                for h in hypotheses:
                    h.exchange = scope["exchange"]
                    h.market_type = scope["market_type"]
                    h.period = scope["period"]
                    if db:
                        self._persist_hypothesis(db, h, "generated")
                self._generated.extend(hypotheses)
                return hypotheses

            messages = [
                {"role": "system", "content": (
                    "You are a quantitative trading researcher. "
                    "Generate actionable trading hypotheses in JSON format. "
                    "Each hypothesis must be specific, testable, and include "
                    "entry/exit conditions with numeric thresholds."
                )},
                {"role": "user", "content": prompt},
            ]

            response = call_llm_api_sync(
                llm_config, messages,
                temperature=0.7, max_tokens=1500,
            )

            if response:
                # P0-2: call_llm_api_sync 返回 dict (API完整响应)，提取 content 文本
                if isinstance(response, dict):
                    choices = response.get("choices", [])
                    if choices and len(choices) > 0:
                        msg = choices[0].get("message", {})
                        text = msg.get("content", "")
                        if not text:
                            logger.warning("[HypothesisEngine] LLM response content 为空")
                            hypotheses = self._fallback_hypotheses(market_context, symbols)
                            for h in hypotheses:
                                h.exchange = scope["exchange"]
                                h.market_type = scope["market_type"]
                                h.period = scope["period"]
                                if db:
                                    self._persist_hypothesis(db, h, "generated")
                            self._generated.extend(hypotheses)
                            return hypotheses
                    else:
                        logger.warning("[HypothesisEngine] LLM response choices 为空")
                        hypotheses = self._fallback_hypotheses(market_context, symbols)
                        for h in hypotheses:
                            h.exchange = scope["exchange"]
                            h.market_type = scope["market_type"]
                            h.period = scope["period"]
                            if db:
                                self._persist_hypothesis(db, h, "generated")
                        self._generated.extend(hypotheses)
                        return hypotheses
                else:
                    text = str(response)
                hypotheses = self._parse_llm_response(text, symbols)
                # 修复（2026-06-24）：LLM 返回了内容但 _parse_llm_response 解析出 0 个假设时
                # （格式不符/JSON 损坏），原代码直接用空列表，导致 generated=0。
                # 现改为解析失败时回退到规则假设，保证假设生成链路不空转。
                if not hypotheses:
                    logger.warning("[HypothesisEngine] LLM 响应解析出 0 个假设, 使用规则回退")
                    hypotheses = self._fallback_hypotheses(market_context, symbols)
            else:
                logger.warning("[HypothesisEngine] LLM 返回空, 使用规则回退")
                hypotheses = self._fallback_hypotheses(market_context, symbols)

        except Exception as e:
            logger.warning(f"[HypothesisEngine] LLM 调用失败: {e}, 使用规则回退")
            hypotheses = self._fallback_hypotheses(market_context, symbols)

        for h in hypotheses:
            h.exchange = scope["exchange"]
            h.market_type = scope["market_type"]
            h.period = scope["period"]
            if db:
                self._persist_hypothesis(db, h, "generated")
        self._generated.extend(hypotheses)
        logger.info(f"[HypothesisEngine] 生成 {len(hypotheses)} 个假设")
        return hypotheses

    def validate_hypothesis(
        self,
        hypothesis: Hypothesis,
        market_context: Optional[Dict[str, Any]] = None,
        db=None,
    ) -> ValidationResult:
        """
        用快速回测验证假设。

        Args:
            hypothesis: 待验证假设

        Returns:
            ValidationResult
        """
        result = ValidationResult(hypothesis_id=hypothesis.hypothesis_id)

        try:
            from backend.services.backtest_evolution_engine import BacktestEngine, Bar

            leverage = float(hypothesis.risk_params.get("leverage", 5))
            engine = BacktestEngine(initial_capital=10000.0, leverage=leverage)

            scope = self._market_scope(market_context)
            exchange = hypothesis.exchange or scope["exchange"]
            period = hypothesis.period or scope["period"]
            klines, data_meta = self._load_klines_from_foundation(
                exchange=exchange,
                symbol=hypothesis.symbol,
                period=period,
                limit=self.BACKTEST_BARS,
            )
            hypothesis.exchange = exchange
            hypothesis.market_type = hypothesis.market_type or scope["market_type"]
            hypothesis.period = period
            hypothesis.snapshot_id = data_meta.get("snapshot_id", "")
            hypothesis.data_source = data_meta.get("data_source", "")
            hypothesis.data_quality = data_meta.get("data_quality", {}) or {}

            if not klines or len(klines) < 50:
                result.error = f"Insufficient data: {len(klines) if klines else 0} bars"
                result.details = data_meta
                if db:
                    self._persist_hypothesis(db, hypothesis, "rejected", result, rejected_reason=result.error)
                self._validated.append(result)
                return result

            # klines → Bar 列表 (BacktestEngine 要求)
            bars: list = []
            for i, row in enumerate(klines if isinstance(klines, list) else klines.itertuples()):
                if isinstance(row, dict):
                    bars.append(Bar(
                        timestamp=int(row.get("timestamp", i)),
                        dt_str=str(row.get("timestamp", i)),
                        o=float(row.get("open", 0)),
                        h=float(row.get("high", 0)),
                        l=float(row.get("low", 0)),
                        c=float(row.get("close", 0)),
                        v=float(row.get("volume", 0)),
                        idx=i,
                    ))
                else:
                    bars.append(Bar(
                        timestamp=int(getattr(row, "timestamp", i) if hasattr(row, "timestamp") else i),
                        dt_str=str(getattr(row, "timestamp", i) if hasattr(row, "timestamp") else i),
                        o=float(getattr(row, "open", 0)),
                        h=float(getattr(row, "high", 0)),
                        l=float(getattr(row, "low", 0)),
                        c=float(getattr(row, "close", 0)),
                        v=float(getattr(row, "volume", 0)),
                        idx=i,
                    ))

            if len(bars) < 50:
                result.error = f"Insufficient bars: {len(bars)}"
                result.details = data_meta
                if db:
                    self._persist_hypothesis(db, hypothesis, "rejected", result, rejected_reason=result.error)
                self._validated.append(result)
                return result

            strategy_config = {
                "direction_bias": hypothesis.direction,
            }
            strategy_config.update(hypothesis.entry_conditions)

            risk_params = {
                "stop_loss_pct": hypothesis.risk_params.get("stop_loss_pct", 0.03),
                "take_profit_pct": hypothesis.risk_params.get("take_profit_pct", 0.06),
                "risk_pct": hypothesis.risk_params.get("risk_pct", 2.0),
            }

            bt_result = engine.run(
                bars=bars,
                strategy_config=strategy_config,
                risk_params=risk_params,
                run_id=hypothesis.hypothesis_id,
            )

            if bt_result:
                result.sharpe = float(bt_result.sharpe_ratio)
                result.win_rate = float(bt_result.win_rate)
                result.max_drawdown_pct = float(bt_result.max_drawdown * 100)
                result.total_trades = int(bt_result.total_trades)
                result.total_pnl = float(bt_result.total_return * 10000)
                result.details = {
                    "sharpe": bt_result.sharpe_ratio,
                    "win_rate": bt_result.win_rate,
                    "max_drawdown": bt_result.max_drawdown,
                    "total_trades": bt_result.total_trades,
                    "total_return": bt_result.total_return,
                    "profit_factor": bt_result.profit_factor,
                    "bars_total": bt_result.bars_total,
                    "exchange": exchange,
                    "period": period,
                    "snapshot_id": hypothesis.snapshot_id,
                    "data_source": hypothesis.data_source,
                    "data_quality": hypothesis.data_quality,
                }

                result.passed = (
                    result.sharpe >= self.MIN_SHARPE
                    and result.win_rate >= self.MIN_WIN_RATE
                    and result.max_drawdown_pct <= self.MAX_DRAWDOWN
                    and result.total_trades >= 3
                )

        except Exception as e:
            result.error = str(e)
            logger.warning(f"[HypothesisEngine] 验证失败 {hypothesis.hypothesis_id}: {e}")

        self._validated.append(result)
        if db:
            status = "validated" if result.passed else "rejected"
            self._persist_hypothesis(
                db,
                hypothesis,
                status,
                result,
                rejected_reason="" if result.passed else (result.error or "validation_threshold_not_met"),
            )
        return result

    def promote_to_evolution(self, hypothesis: Hypothesis, db=None) -> str:
        """
        将达标假设推入策略进化器。

        Returns:
            template_id or "" if failed
        """
        try:
            from backend.services.strategy_evolver import StrategyEvolver

            evolver = StrategyEvolver()

            genome = {
                "trade_nature": "hypothesis_" + hypothesis.direction,
                "entry_conditions": hypothesis.entry_conditions,
                "exit_conditions": hypothesis.exit_conditions,
                "risk_params": hypothesis.risk_params,
                "source": "hypothesis_engine",
                "hypothesis_id": hypothesis.hypothesis_id,
                "hypothesis_description": hypothesis.description[:200],
                "confidence": hypothesis.confidence,
                "exchange": hypothesis.exchange,
                "market_type": hypothesis.market_type,
                "symbol": hypothesis.symbol,
                "period": hypothesis.period,
                "paper_only": True,
            }

            template_id = f"hypo_{uuid.uuid4().hex[:8]}"

            if db:
                try:
                    from backend.database.models import StrategyTemplate
                    # v3 整改: StrategyTemplate 字段名为 strategy_config（非 genome）
                    tpl = StrategyTemplate(
                        template_id=template_id,
                        name=f"Hypothesis: {hypothesis.description[:50]}",
                        description=hypothesis.description[:500],
                        source="hypothesis_engine",
                        strategy_config=genome,
                        is_active=False,
                        tags=["hypothesis_engine", "paper_only"],
                    )
                    db.add(tpl)
                    db.commit()
                    logger.info(
                        f"[HypothesisEngine] 假设晋升为模板 {template_id}: "
                        f"{hypothesis.description[:60]}"
                    )
                except Exception as db_err:
                    logger.warning(f"[HypothesisEngine] 模板入库失败: {db_err}")
                    db.rollback()
                    return ""

            self._promoted_count += 1
            return template_id

        except Exception as e:
            logger.error(f"[HypothesisEngine] 晋升失败: {e}")
            return ""

    def run_full_cycle(
        self,
        market_context: Dict[str, Any],
        symbols: Optional[List[str]] = None,
        db=None,
    ) -> Dict[str, Any]:
        """
        完整运行: 生成 → 验证 → 晋升。

        Returns:
            {generated, validated, promoted, details}
        """
        hypotheses = self.generate_hypotheses(market_context, symbols, db=db)

        validated_results: List[ValidationResult] = []
        promoted_ids: List[str] = []

        for hyp in hypotheses:
            vr = self.validate_hypothesis(hyp, market_context=market_context, db=db)
            validated_results.append(vr)

            if vr.passed:
                tid = self.promote_to_evolution(hyp, db=db)
                if tid:
                    vr.promoted_template_id = tid
                    if db:
                        self._persist_hypothesis(
                            db,
                            hyp,
                            "promoted",
                            vr,
                            promoted_template_id=tid,
                        )
                    promoted_ids.append(tid)

        summary = {
            "generated": len(hypotheses),
            "validated": len(validated_results),
            "passed": sum(1 for v in validated_results if v.passed),
            "promoted": len(promoted_ids),
            "promoted_ids": promoted_ids,
            "details": [
                {
                    "id": v.hypothesis_id,
                    "passed": v.passed,
                    "sharpe": v.sharpe,
                    "win_rate": v.win_rate,
                    "max_dd": v.max_drawdown_pct,
                    "trades": v.total_trades,
                    "promoted_template_id": v.promoted_template_id,
                    "error": v.error,
                }
                for v in validated_results
            ],
        }

        logger.info(
            f"[HypothesisEngine] 完整周期结果: "
            f"generated={summary['generated']}, passed={summary['passed']}, "
            f"promoted={summary['promoted']}"
        )

        return summary

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_generated": len(self._generated),
            "total_validated": len(self._validated),
            "total_promoted": self._promoted_count,
            "recent_pass_rate": (
                sum(1 for v in self._validated[-20:] if v.passed) / max(len(self._validated[-20:]), 1)
            ),
        }

    # ── Prompt building ──────────────────────────

    def _build_prompt(self, ctx: Dict[str, Any], symbols: List[str]) -> str:
        regime = ctx.get("regime", "unknown")
        factor_snap = ctx.get("factor_snapshot", {})
        recent_perf = ctx.get("recent_performance", {})

        prompt = f"""Current market regime: {regime}
Symbols: {', '.join(symbols)}

Factor snapshot (key factors):
{json.dumps(factor_snap, indent=2, default=str)[:800]}

Recent strategy performance:
{json.dumps(recent_perf, indent=2, default=str)[:400]}

Generate {self.HYPOTHESES_PER_RUN} trading hypotheses as a JSON array.
Each hypothesis object must have:
- "description": string (1-2 sentences)
- "symbol": string
- "direction": "long" | "short"
- "entry_conditions": object with factor thresholds
- "exit_conditions": object with stop_loss_pct, take_profit_pct
- "risk_params": object with leverage, risk_pct
- "confidence": float 0-1

Focus on hypotheses that exploit the current {regime} regime.
Respond ONLY with the JSON array, no explanation."""

        return prompt

    def _parse_llm_response(self, response: str, symbols: List[str]) -> List[Hypothesis]:
        """Parse LLM response into Hypothesis objects."""
        hypotheses: List[Hypothesis] = []

        try:
            # Try to extract JSON array from response
            text = response.strip()
            if "```" in text:
                # Extract from code block
                start = text.find("[")
                end = text.rfind("]") + 1
                if start >= 0 and end > start:
                    text = text[start:end]

            items = json.loads(text)
            if not isinstance(items, list):
                items = [items]

            for item in items[:self.HYPOTHESES_PER_RUN]:
                h = Hypothesis(
                    hypothesis_id=f"hyp_{uuid.uuid4().hex[:8]}",
                    description=item.get("description", "LLM hypothesis"),
                    symbol=item.get("symbol", symbols[0] if symbols else "BTC"),
                    direction=item.get("direction", "long"),
                    entry_conditions=item.get("entry_conditions", {}),
                    exit_conditions=item.get("exit_conditions", {}),
                    risk_params=item.get("risk_params", {
                        "stop_loss_pct": 0.03,
                        "take_profit_pct": 0.06,
                        "leverage": 5,
                        "risk_pct": 2.0,
                    }),
                    confidence=float(item.get("confidence", 0.5)),
                    regime_context=str(item.get("regime", "")),
                    source="llm",
                )
                hypotheses.append(h)

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"[HypothesisEngine] LLM 解析失败: {e}")

        return hypotheses

    # ── Fallback (rule-based) ────────────────────

    def _fallback_hypotheses(
        self, ctx: Dict[str, Any], symbols: List[str]
    ) -> List[Hypothesis]:
        """当 LLM 不可用时的规则回退假设。"""
        regime = ctx.get("regime", "ranging")
        hypotheses: List[Hypothesis] = []

        for sym in symbols[:2]:
            if regime in ("uptrend", "bull_volatile"):
                hypotheses.append(Hypothesis(
                    hypothesis_id=f"hyp_{uuid.uuid4().hex[:8]}",
                    description=f"Trend-following long on {sym} in {regime}",
                    symbol=sym,
                    direction="long",
                    entry_conditions={"rsi_14": {"gt": 50}, "adx": {"gt": 20}},
                    exit_conditions={"stop_loss_pct": 0.03, "take_profit_pct": 0.08},
                    risk_params={"leverage": 5, "risk_pct": 2.0,
                                 "stop_loss_pct": 0.03, "take_profit_pct": 0.08},
                    confidence=0.6,
                    regime_context=regime,
                    source="rule_fallback",
                ))
            elif regime in ("downtrend", "bear_volatile"):
                hypotheses.append(Hypothesis(
                    hypothesis_id=f"hyp_{uuid.uuid4().hex[:8]}",
                    description=f"Trend-following short on {sym} in {regime}",
                    symbol=sym,
                    direction="short",
                    entry_conditions={"rsi_14": {"lt": 45}, "adx": {"gt": 20}},
                    exit_conditions={"stop_loss_pct": 0.03, "take_profit_pct": 0.07},
                    risk_params={"leverage": 5, "risk_pct": 2.0,
                                 "stop_loss_pct": 0.03, "take_profit_pct": 0.07},
                    confidence=0.55,
                    regime_context=regime,
                    source="rule_fallback",
                ))
            else:
                hypotheses.append(Hypothesis(
                    hypothesis_id=f"hyp_{uuid.uuid4().hex[:8]}",
                    description=f"Mean-reversion on {sym} in {regime}",
                    symbol=sym,
                    direction="long",
                    entry_conditions={"bb_position": {"lt": 0.2}, "rsi_14": {"lt": 35}},
                    exit_conditions={"stop_loss_pct": 0.025, "take_profit_pct": 0.04},
                    risk_params={"leverage": 3, "risk_pct": 1.5,
                                 "stop_loss_pct": 0.025, "take_profit_pct": 0.04},
                    confidence=0.5,
                    regime_context=regime,
                    source="rule_fallback",
                ))

        return hypotheses


# Global singleton
_engine: Optional[StrategyHypothesisEngine] = None


def get_hypothesis_engine() -> StrategyHypothesisEngine:
    global _engine
    if _engine is None:
        _engine = StrategyHypothesisEngine()
    return _engine
