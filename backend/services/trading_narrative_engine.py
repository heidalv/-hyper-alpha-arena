"""
交易叙事引擎 — P1.3
每24h生成连贯的交易叙事，注入 MasterController 决策上下文。
让AI不只是看到"过去24h有15笔交易"，而是理解"这段时间市场上发生了什么故事"。

加密适配：
- 减半周期/ETF叙事/监管事件等长周期叙事
- 周末流动性陷阱识别
- 资金费率极端值的社会意义（市场过度杠杆/过度恐慌）
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_NARRATIVE_CACHE_FILE = os.path.join("data", "opencode_reports", "latest_narrative.json")
_NARRATIVE_SYSTEM_PROMPT_PATH = os.path.join(
    "backend", "prompts", "opencode_narrative_system.md"
)


def _load_narrative_system_prompt() -> str:
    if os.path.isfile(_NARRATIVE_SYSTEM_PROMPT_PATH):
        with open(_NARRATIVE_SYSTEM_PROMPT_PATH, encoding="utf-8") as f:
            return f.read()
    return (
        "You are Alpha Arena Trading Narrative Engine.\n"
        "Your job is to construct a coherent trading narrative from raw trade data.\n"
        "You understand that markets tell stories, not just generate numbers.\n\n"
        "CRYPTO-SPECIFIC NARRATIVE AWARENESS:\n"
        "- Bitcoin halving cycles (every 4 years) are dominant narrative drivers\n"
        "- ETF approvals/rejections cause regime shifts\n"
        "- Regulatory crackdowns create panic narratives\n"
        "- Exchange solvency events (FTX-style) create systemic risk narratives\n"
        "- Meme coin manias are sentiment-driven, not fundamental\n"
        "- Weekend trading has unique low-liquidity characteristics\n\n"
        "Your output should read like a professional macro trader's daily journal.\n"
        "Return ONLY valid JSON. No markdown fences."
    )


class TradingNarrativeEngine:
    """交易叙事引擎 — 从原始交易数据中提炼市场叙事"""

    _instance: Optional["TradingNarrativeEngine"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._lock = threading.Lock()
        self._last_build_ts: float = 0.0
        self._cached_narrative: Optional[Dict[str, Any]] = None
        # 从文件恢复缓存
        self._load_cache()
        logger.info("[NarrativeEngine] 交易叙事引擎初始化完成")

    @classmethod
    def get_instance(cls) -> "TradingNarrativeEngine":
        return cls()

    def _is_enabled(self) -> bool:
        try:
            from backend.config.settings import AI_NARRATIVE_ENGINE_ENABLED
            return bool(AI_NARRATIVE_ENGINE_ENABLED)
        except Exception:
            return False

    def _load_cache(self):
        """从文件恢复叙事缓存"""
        try:
            if os.path.isfile(_NARRATIVE_CACHE_FILE):
                with open(_NARRATIVE_CACHE_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                    self._cached_narrative = data
                    self._last_build_ts = data.get("build_ts", 0)
                    logger.debug("[NarrativeEngine] 已从缓存恢复叙事")
        except Exception:
            pass

    def _save_cache(self):
        """持久化叙事缓存到文件"""
        try:
            os.makedirs(os.path.dirname(_NARRATIVE_CACHE_FILE), exist_ok=True)
            with open(_NARRATIVE_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(self._cached_narrative or {}, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("[NarrativeEngine] 缓存保存失败: %s", exc)

    def get_current_narrative(self) -> Optional[Dict[str, Any]]:
        """获取当前缓存的交易叙事"""
        if not self._is_enabled():
            return None
        return self._cached_narrative

    def get_narrative_for_prompt(self) -> str:
        """生成可注入 AI prompt 的叙事文本"""
        if not self._is_enabled() or not self._cached_narrative:
            return ""

        n = self._cached_narrative
        parts = [
            "## 📖 近期市场叙事",
            "",
            f"**主导趋势**: {n.get('dominant_trend', 'unknown')}",
            f"**叙事主题**: {n.get('narrative_theme', '无')}",
            "",
            f"**过去24h摘要**: {n.get('narrative', '')[:500]}",
            "",
            "### 三周期状态",
            f"- 短期(15m): {n.get('short_term_state', '?')}",
            f"- 中期(1h): {n.get('mid_term_state', '?')}",
            f"- 长期(4h): {n.get('long_term_state', '?')}",
            "",
            "### AI的自我觉察",
            f"- 最近常见错误: {n.get('recent_common_mistake', '?')}",
            f"- 应警惕的模式: {n.get('pattern_to_watch', '?')}",
            "",
            "### 加密专属信号",
            f"- 资金费率状态: {n.get('funding_rate_status', '?')}",
            f"- 周末流动性: {n.get('weekend_liquidity_note', '?')}",
            f"- BTC锚定效应: {n.get('btc_correlation_note', '?')}",
        ]
        return "\n".join(parts)

    def build_narrative(self, db) -> Optional[Dict[str, Any]]:
        """
        构建24h交易叙事。

        从数据库获取最近24h的交易、市场状态、OpenCode教训，
        调用 LLM 生成连贯的市场叙事。
        """
        if not self._is_enabled():
            return None

        # 节流：24h内不重复构建
        now_ts = datetime.now(timezone.utc).timestamp()
        if now_ts - self._last_build_ts < 23 * 3600:
            logger.debug("[NarrativeEngine] 距上次构建不足23h，使用缓存")
            return self._cached_narrative

        from backend.services.opencode_bridge import (
            run_http_agent_message, _extract_json,
            _is_enabled as _oc_enabled, _agent_plan, _model,
        )

        if not _oc_enabled():
            return None

        try:
            from backend.database.models import StrategyTrade, StrategyMemory

            # 1. 收集24h交易数据
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            recent_trades = (
                db.query(StrategyTrade)
                .filter(
                    StrategyTrade.status == "closed",
                    StrategyTrade.closed_at >= cutoff,
                    ~StrategyTrade.strategy_id.like("rebate_%"),
                )
                .order_by(StrategyTrade.closed_at.asc())
                .all()
            )

            if len(recent_trades) < 5:
                logger.debug(
                    f"[NarrativeEngine] 近24h仅{len(recent_trades)}笔交易，跳过叙事构建"
                )
                return self._cached_narrative

            # 2. 聚合统计
            total_pnl = sum(float(t.pnl or 0) for t in recent_trades)
            win_count = sum(1 for t in recent_trades if (t.pnl or 0) > 0)
            symbols = list(set(t.symbol for t in recent_trades if t.symbol))

            # 按小时聚合PnL
            pnl_by_hour: Dict[int, float] = {}
            for t in recent_trades:
                if t.closed_at:
                    hour = t.closed_at.hour
                    pnl_by_hour[hour] = pnl_by_hour.get(hour, 0) + float(t.pnl or 0)

            # 3. 收集最近 OpenCode 教训
            global_memory = db.query(StrategyMemory).filter(
                StrategyMemory.strategy_id == "_global_"
            ).first()
            recent_lessons = []
            if global_memory and global_memory.key_lessons:
                recent_lessons = [
                    l for l in global_memory.key_lessons[-10:]
                    if isinstance(l, dict) and l.get("ts", "")
                ]

            # 4. 计算加密特异性指标
            # 周末交易识别
            weekend_trades = [
                t for t in recent_trades
                if t.closed_at and t.closed_at.weekday() >= 5
            ]
            weekend_pnl = sum(float(t.pnl or 0) for t in weekend_trades)
            weekday_pnl = total_pnl - weekend_pnl

            # 5. 构建 prompt
            system = _load_narrative_system_prompt()

            trade_summaries = []
            for t in recent_trades[:50]:
                trade_summaries.append({
                    "symbol": t.symbol,
                    "side": t.side,
                    "pnl": round(float(t.pnl or 0), 2),
                    "closed_at": str(t.closed_at)[:19] if t.closed_at else "?",
                    "strategy_id": t.strategy_id,
                })

            user_text_parts = [
                "## 交易叙事构建请求（24h）",
                "",
                f"### 总体统计",
                f"- 总交易: {len(recent_trades)}笔",
                f"- 胜率: {win_count}/{len(recent_trades)} ({win_count/len(recent_trades):.1%})",
                f"- 总PnL: ${total_pnl:+.2f}",
                f"- 涉及币种: {', '.join(symbols[:8])}",
                "",
                f"### 加密特异性",
                f"- 周末交易: {len(weekend_trades)}笔, PnL=${weekend_pnl:+.2f}",
                f"- 工作日交易: {len(recent_trades)-len(weekend_trades)}笔, PnL=${weekday_pnl:+.2f}",
                "",
                "### 时段PnL分布（UTC小时）",
            ]
            for h in sorted(pnl_by_hour.keys()):
                user_text_parts.append(f"  {h:02d}:00 — ${pnl_by_hour[h]:+.2f}")

            user_text_parts.extend([
                "",
                "### 最近教训",
                json.dumps(recent_lessons, ensure_ascii=False, indent=2),
                "",
                "### 交易明细",
                json.dumps(trade_summaries, ensure_ascii=False, indent=2),
                "",
                "---",
                "## 叙事构建要求",
                "",
                "基于以上数据，构建一个连贯的24h交易叙事。叙事应包含：",
                "1. **主导趋势识别**：市场在这24h内是什么状态？",
                "2. **叙事主题**：提炼1-2个关键词（如\"BTC突破后的山寨季\"\"周末流动性陷阱\"）",
                "3. **三周期状态**：短期/中期/长期的节奏感",
                "4. **AI自我觉察**：这段时间AI犯了什么错误？错过了什么？",
                "5. **加密特异性**：资金费率状态、周末效应、BTC锚定分析",
                "",
                "输出JSON格式：",
                "{",
                "  \"dominant_trend\": \"bullish|bearish|ranging|volatile\",",
                "  \"narrative_theme\": \"简短主题（≤30字）\",",
                "  \"narrative\": \"完整叙事文本（200-500字）\",",
                "  \"short_term_state\": \"...\",",
                "  \"mid_term_state\": \"...\",",
                "  \"long_term_state\": \"...\",",
                "  \"recent_common_mistake\": \"...\",",
                "  \"pattern_to_watch\": \"...\",",
                "  \"funding_rate_status\": \"normal|elevated|extreme\",",
                "  \"weekend_liquidity_note\": \"...\",",
                "  \"btc_correlation_note\": \"...\",",
                "  \"confidence\": 0.0",
                "}",
            ])

            user_text = "\n".join(user_text_parts)

            raw, err = run_http_agent_message(
                system_prompt=system,
                user_text=user_text,
                agent=_agent_plan(),
                model_slug=_model(),
                session_title="Trading Narrative 24h",
            )

            if err:
                logger.warning(f"[NarrativeEngine] 叙事构建失败: {err}")
                return self._cached_narrative

            result = _extract_json(raw or "")

            # 更新缓存
            with self._lock:
                narrative = {
                    **result,
                    "build_ts": now_ts,
                    "build_time": datetime.now(timezone.utc).isoformat(),
                    "trade_count": len(recent_trades),
                    "total_pnl": total_pnl,
                    "win_rate": win_count / max(len(recent_trades), 1),
                }
                self._cached_narrative = narrative
                self._last_build_ts = now_ts
                self._save_cache()

            logger.info(
                f"[NarrativeEngine] 24h叙事已构建: "
                f"trend={result.get('dominant_trend', '?')} "
                f"theme={result.get('narrative_theme', '?')} "
                f"confidence={result.get('confidence', 0)}"
            )

            return self._cached_narrative

        except Exception as exc:
            logger.error("[NarrativeEngine] build_narrative: %s", exc, exc_info=True)
            return self._cached_narrative


# 全局单例
trading_narrative_engine = TradingNarrativeEngine.get_instance()
