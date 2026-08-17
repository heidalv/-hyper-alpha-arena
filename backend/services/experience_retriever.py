"""
经验检索器 — 从多源知识库中检索历史类比

升级路线：
  V1 (原始): SQL 精确匹配 DecisionSnapshot (symbol + regime)
  V2 (当前): RAG 向量语义检索，跨 5 个 Collection 检索
             - trade_decisions: 历史交易决策快照
             - strategy_lessons: 策略教训 (StrategyMemory.key_lessons)
             - trading_wisdom: 交易智慧 (TradingWisdom)
             - trade_memory: 交易记忆聚合 (TradeMemoryRecord)
             - static_knowledge: K线形态百科 + 宏观事件案例

接口兼容：format_for_prompt(db, symbols, regime) 签名不变，
          MasterController 调用点零修改。

降级机制：RAG 不可用时自动回退 SQL 检索。
"""

import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class ExperienceRetriever:
    """历史经验检索器（单例，线程安全）"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    # ------------------------------------------------------------------
    #  RAG 语义检索（主路径）
    # ------------------------------------------------------------------

    def _rag_retrieve(
        self,
        symbols: List[str],
        regime: str = None,
        top_k: int = 3,
    ) -> Optional[Dict[str, List[Dict]]]:
        """
        使用 RAG 向量检索跨 collection 获取相关知识。
        返回 None 表示 RAG 不可用。
        """
        try:
            from backend.services.rag_knowledge_service import rag_knowledge_service

            # 不再提前检查 is_ready — retrieve() 内部会调用 _ensure_ready() 自动初始化
            # 旧逻辑: is_ready 在延迟加载模式下永远 False → RAG 永远不被使用
            query_parts = []
            for sym in symbols[:5]:
                query_parts.append(sym)
            if regime:
                query_parts.append(f"市场环境:{regime}")

            query_text = " ".join(query_parts) + " 交易决策参考"

            raw_results = rag_knowledge_service.retrieve(
                query_text=query_text,
                top_k=top_k,
            )

            if not raw_results:
                return None

            return raw_results
        except Exception as e:
            logger.debug(f"[ExperienceRetriever] RAG 检索异常，将降级到SQL: {e}")
            return None

    def _format_rag_results(self, raw_results: Dict[str, List[Dict]]) -> str:
        """将 RAG 检索结果格式化为 prompt 文本"""
        from backend.services.rag_knowledge_service import (
            COLL_TRADE_DECISIONS,
            COLL_STRATEGY_LESSONS,
            COLL_TRADING_WISDOM,
            COLL_TRADE_MEMORY,
            COLL_STATIC_KNOWLEDGE,
        )

        sections = []

        section_config = [
            (COLL_TRADE_DECISIONS, "相似历史交易", self._fmt_trade_decision),
            (COLL_STRATEGY_LESSONS, "策略经验教训", self._fmt_strategy_lesson),
            (COLL_TRADING_WISDOM, "交易智慧参考", self._fmt_trading_wisdom),
            (COLL_TRADE_MEMORY, "品种行为模式", self._fmt_trade_memory),
            (COLL_STATIC_KNOWLEDGE, "形态/事件参考", self._fmt_static_knowledge),
        ]

        for coll_name, title, formatter in section_config:
            items = raw_results.get(coll_name, [])
            if not items:
                continue

            lines = [f"\n**{title}:**"]
            for item in items[:3]:
                line = formatter(item)
                if line:
                    lines.append(f"  - {line}")
            if len(lines) > 1:
                sections.append("\n".join(lines))

        if not sections:
            return ""

        header = "### 历史类比与知识参考（RAG 语义检索）"
        footer = (
            "\n请参考以上历史类比和知识参考。"
            "相似市况下避免重复失败模式，优先采用历史上成功的策略方向。"
        )
        return header + "\n" + "\n".join(sections) + "\n" + footer

    def _fmt_trade_decision(self, item: Dict) -> str:
        meta = item.get("metadata", {})
        symbol = meta.get("symbol", "?")
        regime = meta.get("regime", "?")
        pnl = meta.get("pnl_pct", 0)
        direction = meta.get("direction", "?")
        conf = meta.get("confidence", 0)
        quality = meta.get("quality", "")
        result_icon = "+" if pnl > 0 else "-"
        return (
            f"{symbol} {direction} 置信{conf:.0f}%: "
            f"{result_icon}{abs(pnl):.1f}% ({regime}) {quality}"
        )

    def _fmt_strategy_lesson(self, item: Dict) -> str:
        text = item.get("text", "")
        # 截取核心教训内容
        if "策略教训" in text:
            parts = text.split("|")
            return parts[0].strip()[:150]
        return text[:150]

    def _fmt_trading_wisdom(self, item: Dict) -> str:
        text = item.get("text", "")
        if "交易智慧" in text:
            parts = text.split("|")
            return parts[0].strip()[:150]
        return text[:150]

    def _fmt_trade_memory(self, item: Dict) -> str:
        text = item.get("text", "")
        return text[:150]

    def _fmt_static_knowledge(self, item: Dict) -> str:
        text = item.get("text", "")
        meta = item.get("metadata", {})
        source = meta.get("source", "")
        if source == "kline_pattern":
            parts = text.split("|")
            return parts[0].strip()[:150]
        elif source == "macro_event":
            parts = text.split("|")
            if len(parts) >= 3:
                return f"{parts[0].strip()} | {parts[2].strip()}"[:150]
        return text[:150]

    # ------------------------------------------------------------------
    #  SQL 降级检索（V1 原始逻辑，保留作为后备）
    # ------------------------------------------------------------------

    def _sql_retrieve_analogies(
        self,
        db: Session,
        symbol: str,
        regime: str = None,
        days: int = 30,
        top_n: int = 3,
    ) -> Dict[str, List[Dict]]:
        """SQL 精确匹配检索（降级路径）

        注意: DecisionSnapshot 表在 alpha_analytics 数据库中，
        不能使用传入的 db（可能是 alpha_arena 的 session），
        否则会触发 UndefinedTable → InFailedSqlTransaction 污染。
        """
        _own_db = None
        try:
            from backend.database.models import DecisionSnapshot
            from backend.database.connection import AnalyticsSessionLocal

            _own_db = AnalyticsSessionLocal()
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)

            query = _own_db.query(DecisionSnapshot).filter(
                DecisionSnapshot.symbol == symbol,
                DecisionSnapshot.timestamp >= cutoff,
                DecisionSnapshot.pnl.isnot(None),
                # [P0-4 消费端防御] 经验检索只吃已执行决策，避免错配 pnl 注入提示词
                DecisionSnapshot.executed == True,  # noqa: E712
            )

            if regime:
                query = query.filter(DecisionSnapshot.regime_at_decision == regime)

            snapshots = query.order_by(DecisionSnapshot.timestamp.desc()).limit(200).all()

            if not snapshots:
                return {"good": [], "bad": [], "stats": {"total": 0, "win_rate": 0}}

            wins = [s for s in snapshots if (s.pnl or 0) > 0]
            losses = [s for s in snapshots if (s.pnl or 0) <= 0]
            total = len(snapshots)
            win_rate = len(wins) / total if total > 0 else 0

            wins.sort(key=lambda s: s.pnl or 0, reverse=True)
            losses.sort(key=lambda s: s.pnl or 0)

            def _snap_to_dict(s) -> Dict:
                return {
                    "date": s.timestamp.strftime("%m/%d") if s.timestamp else "?",
                    "symbol": s.symbol,
                    "action": s.action or "?",
                    "direction": s.direction or "?",
                    "confidence": s.confidence or 0,
                    "pnl_pct": round((s.pnl_pct or 0) * 100, 2),
                    "regime": s.regime_at_decision or "?",
                    "quality": s.quality_label or "?",
                    "lesson": (s.lesson_extracted or "")[:100],
                }

            return {
                "good": [_snap_to_dict(s) for s in wins[:top_n]],
                "bad": [_snap_to_dict(s) for s in losses[:top_n]],
                "stats": {
                    "total": total,
                    "win_rate": round(win_rate, 3),
                    "avg_win_pct": round(sum((s.pnl_pct or 0) for s in wins) / max(len(wins), 1) * 100, 2),
                    "avg_loss_pct": round(sum((s.pnl_pct or 0) for s in losses) / max(len(losses), 1) * 100, 2),
                },
            }

        except Exception as e:
            logger.debug(f"[ExperienceRetriever] SQL 检索失败: {e}")
            return {"good": [], "bad": [], "stats": {"total": 0, "win_rate": 0}}
        finally:
            if _own_db:
                try:
                    _own_db.close()
                except Exception:
                    pass

    def _format_sql_results(
        self,
        db: Session,
        symbols: List[str],
        regime: str = None,
    ) -> str:
        """SQL 检索结果格式化为 prompt（降级路径）"""
        all_lines = ["### 历史类比（来自你的交易经验）"]
        has_data = False

        for sym in symbols[:5]:
            result = self._sql_retrieve_analogies(db, sym, regime=regime, days=30)
            if result["stats"]["total"] == 0:
                continue

            has_data = True
            stats = result["stats"]
            all_lines.append(
                f"\n**{sym}** 最近30天: {stats['total']}笔决策 "
                f"胜率{stats['win_rate']*100:.0f}%"
            )

            if result["good"]:
                for g in result["good"][:2]:
                    all_lines.append(
                        f"  + {g['date']} {g['direction']} conf={g['confidence']:.0f}%: "
                        f"{g['pnl_pct']:+.1f}% ({g['regime']})"
                    )
            if result["bad"]:
                for b in result["bad"][:2]:
                    line = (
                        f"  - {b['date']} {b['direction']} conf={b['confidence']:.0f}%: "
                        f"{b['pnl_pct']:+.1f}% ({b['regime']})"
                    )
                    if b.get("lesson"):
                        line += f" | 教训: {b['lesson']}"
                    all_lines.append(line)

        if not has_data:
            return ""

        all_lines.append("")
        all_lines.append(
            "请参考以上历史类比，相似市况下避免重复失败模式，"
            "优先采用历史上成功的策略方向。"
        )
        return "\n".join(all_lines)

    # ------------------------------------------------------------------
    #  公共接口（保持签名不变）
    # ------------------------------------------------------------------

    def retrieve_analogies(
        self,
        db: Session,
        symbol: str,
        regime: str = None,
        tier: str = None,
        days: int = 30,
        top_n: int = 3,
    ) -> Dict[str, List[Dict]]:
        """
        检索指定 symbol 在相似市况下的历史决策记录。
        tier 参数保留以向后兼容。
        """
        return self._sql_retrieve_analogies(db, symbol, regime, days, top_n)

    def format_for_prompt(
        self,
        db: Session,
        symbols: List[str],
        regime: str = None,
    ) -> str:
        """
        格式化为可注入 MasterController prompt 的文本段。

        优先使用 RAG 语义检索（跨5个知识源），
        RAG 不可用时降级到 SQL 精确匹配。
        """
        # 主路径: RAG 语义检索
        rag_results = self._rag_retrieve(symbols, regime)
        if rag_results:
            formatted = self._format_rag_results(rag_results)
            if formatted:
                total_items = sum(len(v) for v in rag_results.values())
                coll_names = list(rag_results.keys())
                logger.info(f"[ExperienceRetriever] RAG 检索成功，{len(rag_results)} 个源命中({total_items}条)，collections={coll_names}")
                return formatted

        # 降级路径: SQL 检索
        logger.info("[ExperienceRetriever] RAG 无结果或不可用，降级到 SQL 检索")
        return self._format_sql_results(db, symbols, regime)


experience_retriever = ExperienceRetriever()
