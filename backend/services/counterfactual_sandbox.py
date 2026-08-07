"""
反事实推理沙盒 — P1.2
每笔大亏(>$50)后，利用 OpenCode 生成反事实场景：
"如果当时做了不同的决策会怎样？"

用途：
1. 亏损后自动触发，生成 3 个反事实场景
2. 每个场景包含：假设条件、预期结果、实际教训
3. 反事实教训写入 StrategyMemory，注入到后续 prompt
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_COUNTERFACTUAL_SYSTEM_PROMPT_PATH = os.path.join(
    "backend", "prompts", "opencode_counterfactual_system.md"
)


def _load_cf_system_prompt() -> str:
    if os.path.isfile(_COUNTERFACTUAL_SYSTEM_PROMPT_PATH):
        with open(_COUNTERFACTUAL_SYSTEM_PROMPT_PATH, encoding="utf-8") as f:
            return f.read()
    return (
        "You are Alpha Arena counterfactual reasoning engine.\n"
        "Given a real trade that resulted in a significant loss, "
        "you generate 3 counterfactual scenarios:\n"
        "1. What if we had a tighter stop-loss?\n"
        "2. What if we had delayed entry by 1 candle?\n"
        "3. What if we had recognized a regime shift earlier?\n\n"
        "For crypto specifically:\n"
        "- Consider funding rate extremes as potential reversal signals\n"
        "- Consider weekend liquidity traps as exit signals\n"
        "- Consider BTC correlation as a hedge-decision factor\n\n"
        "Return ONLY valid JSON. No markdown fences."
    )


class CounterfactualSandbox:
    """反事实推理沙盒 — 亏损后生成假设性替代场景"""

    _instance: Optional["CounterfactualSandbox"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._queue: list = []
        self._lock = threading.Lock()
        self._worker_started = False
        logger.info("[CounterfactualSandbox] 反事实推理沙盒初始化完成")

    @classmethod
    def get_instance(cls) -> "CounterfactualSandbox":
        return cls()

    def _is_enabled(self) -> bool:
        try:
            from backend.config.settings import AI_COUNTERFACTUAL_SANDBOX_ENABLED
            return bool(AI_COUNTERFACTUAL_SANDBOX_ENABLED)
        except Exception:
            return False

    def _start_worker(self):
        """启动后台消费线程"""
        if self._worker_started:
            return
        self._worker_started = True
        t = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="counterfactual-worker",
        )
        t.start()
        logger.info("[CounterfactualSandbox] 后台消费线程已启动")

    def enqueue(
        self,
        db,
        trade_context: Dict[str, Any],
        loss_threshold: float = 50.0,
    ):
        """
        入队一笔亏损交易的反事实分析请求。

        Args:
            db: 数据库会话（主库）
            trade_context: 交易上下文
                {
                    "symbol": "BTC",
                    "side": "long",
                    "pnl": -120.5,
                    "pnl_pct": -0.035,
                    "entry_price": 92500,
                    "exit_price": 89300,
                    "close_reason": "stop_loss",
                    "duration_min": 45,
                    "strategy_id": "xxx",
                    "market_snapshot": {...},
                    "orchestrator_view": {...},
                }
            loss_threshold: 触发阈值（亏损金额绝对值）
        """
        if not self._is_enabled():
            return

        abs_pnl = abs(float(trade_context.get("pnl", 0)))
        if abs_pnl < loss_threshold:
            return

        self._start_worker()
        with self._lock:
            self._queue.append({
                "db_session_factory": db.__class__,
                "trade_context": trade_context,
                "enqueued_at": datetime.now(timezone.utc).isoformat(),
            })
        logger.info(
            f"[CounterfactualSandbox] 已入队: {trade_context.get('symbol')} "
            f"PnL=${trade_context.get('pnl', 0):+.2f} "
            f"(队列长度: {len(self._queue)})"
        )

    def _worker_loop(self):
        """后台消费循环"""
        import time as _time
        while True:
            try:
                item = None
                with self._lock:
                    if self._queue:
                        item = self._queue.pop(0)

                if item is None:
                    _time.sleep(5)
                    continue

                self._process_one(item)
            except Exception as exc:
                logger.error(
                    "[CounterfactualSandbox] worker异常: %s", exc, exc_info=True
                )

    def _process_one(self, item: Dict[str, Any]):
        """处理单条反事实推理请求"""
        trade = item["trade_context"]

        try:
            env_level = 0
            try:
                from backend.config.settings import AI_EVOLUTION_LEVEL
                env_level = int(AI_EVOLUTION_LEVEL)
            except Exception:
                pass
            if env_level < 3:
                logger.debug("[CounterfactualSandbox] AI_EVOLUTION_LEVEL<3，跳过反事实分析")
                return
        except Exception:
            pass

        from backend.services.opencode_bridge import (
            run_http_agent_message, _extract_json,
            _is_enabled as _oc_enabled, _agent_plan, _model,
        )

        if not _oc_enabled():
            logger.debug("[CounterfactualSandbox] OpenCode未启用，跳过")
            return

        system = _load_cf_system_prompt()

        context_json = json.dumps(trade, ensure_ascii=False, indent=2)

        user_text = (
            f"## 反事实推理：{trade.get('symbol')} 亏损分析\n\n"
            f"### 实际交易详情\n{context_json}\n\n"
            f"### 分析要求\n\n"
            f"请基于以上真实交易，生成 **3 个反事实场景**：\n\n"
            f"**场景1：更紧止损**\n"
            f"- 假设止损设在当前止损的 60% 位置，该笔交易结果如何？\n"
            f"- 是否会触发之后的反转？还是会错过后续行情？\n\n"
            f"**场景2：延迟入场**\n"
            f"- 假设延迟 1 根K线（{trade.get('timeframe', '15m')}）入场，结果如何？\n"
            f"- 假突破的概率是多少？\n\n"
            f"**场景3：市场体制识别**\n"
            f"- 入场时的资金费率、OI、爆仓数据是否已经暗示方向可能反转？\n"
            f"- 是否应该识别出周末低流动性陷阱或极端费率信号？\n\n"
            f"### 加密特异性要求\n"
            f"- 检查资金费率绝对值是否 >0.10%（极端值）\n"
            f"- 检查入场时间是否在周末（UTC周六00:00~周一00:00）\n"
            f"- 检查BTC是否同日同向大幅波动（山寨币锚定效应）\n\n"
            f"输出JSON格式：\n"
            f"{{\n"
            f"  \"scenarios\": [\n"
            f"    {{\n"
            f"      \"name\": \"tighter_stop|delayed_entry|regime_aware\",\n"
            f"      \"hypothetical_pnl\": 0.0,\n"
            f"      \"confidence\": 0.0,\n"
            f"      \"key_insight\": \"...\",\n"
            f"      \"actionable_lesson\": \"...\"\n"
            f"    }}\n"
            f"  ],\n"
            f"  \"dominant_mistake\": \"tight_stop|late_entry|regime_blindness|funding_trap|weekend_trap\",\n"
            f"  \"overall_lesson\": \"...\"\n"
            f"}}"
        )

        raw, err = run_http_agent_message(
            system_prompt=system,
            user_text=user_text,
            agent=_agent_plan(),
            model_slug=_model(),
            session_title=f"Counterfactual: {trade.get('symbol')}",
        )

        if err:
            logger.warning(
                f"[CounterfactualSandbox] 反事实分析失败: {err}"
            )
            return

        result = _extract_json(raw or "")
        scenarios = result.get("scenarios", [])

        if not scenarios or len(scenarios) < 1:
            logger.debug("[CounterfactualSandbox] 反事实分析未产出有效场景")
            return

        # 写入 StrategyMemory
        try:
            strategy_id = trade.get("strategy_id", "")
            if not strategy_id:
                return

            from backend.database.models import StrategyMemory
            DbClass = item["db_session_factory"]
            db = DbClass()
            try:
                memory = db.query(StrategyMemory).filter(
                    StrategyMemory.strategy_id == strategy_id
                ).first()
                if memory:
                    lessons = list(memory.key_lessons or [])
                    for sc in scenarios[:3]:
                        lessons.append({
                            "type": "counterfactual",
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "symbol": trade.get("symbol"),
                            "original_pnl": trade.get("pnl"),
                            "scenario": sc.get("name"),
                            "hypothetical_pnl": sc.get("hypothetical_pnl"),
                            "insight": sc.get("key_insight", ""),
                            "actionable_lesson": sc.get("actionable_lesson", ""),
                            "dominant_mistake": result.get("dominant_mistake", ""),
                        })
                    memory.key_lessons = lessons[-50:]
                    memory.overall_lesson = result.get("overall_lesson", "")[:2000]
                    db.commit()
                    logger.info(
                        f"[CounterfactualSandbox] 反事实教训已写入 StrategyMemory "
                        f"{strategy_id}: {len(scenarios)}个场景"
                    )
            finally:
                db.close()
        except Exception as me:
            logger.debug(
                "[CounterfactualSandbox] StrategyMemory写入失败: %s", me
            )

        logger.info(
            f"[CounterfactualSandbox] 反事实分析完成: "
            f"symbol={trade.get('symbol')} "
            f"scenarios={len(scenarios)} "
            f"dominant_mistake={result.get('dominant_mistake', '?')}"
        )


# 全局单例
counterfactual_sandbox = CounterfactualSandbox.get_instance()
