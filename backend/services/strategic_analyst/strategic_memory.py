"""
Strategic Analyst - 长期记忆系统

三层记忆架构：
1. 短期缓存 (LRU, 最近50条) - 最近战略报告的缓存
2. 结构化记忆 (DB) - 持久化的战略经验，支持验证/证伪
3. 规则提取 - 从反复验证的记忆中自动提取规则

记忆类型：
- macro_lesson: 宏观经验
- cycle_pattern: 周期模式
- new_coin_postmortem: 新币复盘
- regime_transition: 体制转换经验
"""

import logging
import json
import threading
from typing import Dict, List, Optional
from collections import OrderedDict
from datetime import datetime, timedelta

from .models import StrategicMemory

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LRU 缓存
# ---------------------------------------------------------------------------
class _LRUCache:
    """简单的 LRU 缓存"""

    def __init__(self, maxsize: int = 50):
        self._cache: OrderedDict = OrderedDict()
        self._maxsize = maxsize
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        return None

    def put(self, key, value):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = value
            while len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)

    def values(self):
        with self._lock:
            return list(self._cache.values())

    def __len__(self):
        return len(self._cache)


# ---------------------------------------------------------------------------
# 战略记忆系统
# ---------------------------------------------------------------------------
class StrategicMemorySystem:
    """
    长期记忆系统

    存储和检索战略级经验，支持验证/证伪和规则提取
    """

    # 规则提取阈值
    RULE_VALIDATION_THRESHOLD = 5    # 被验证次数 >= 5
    RULE_CONFIDENCE_THRESHOLD = 0.7  # 置信度 >= 0.7

    def __init__(self):
        self._cache = _LRUCache(maxsize=50)
        self._extracted_rules: List[Dict] = []
        self._db_session_factory = None
        # 默认使用 AnalyticsSessionLocal
        try:
            from backend.database.connection import AnalyticsSessionLocal
            self._db_session_factory = AnalyticsSessionLocal
        except ImportError:
            try:
                from database.connection import AnalyticsSessionLocal
                self._db_session_factory = AnalyticsSessionLocal
            except Exception:
                pass

    def set_db_session_factory(self, factory):
        """设置数据库 session 工厂"""
        self._db_session_factory = factory

    # -----------------------------------------------------------------------
    # 核心 API
    # -----------------------------------------------------------------------

    def store_observation(
        self,
        memory_type: str,
        market_context: str,
        observation: str,
        lesson: str,
        applicability_conditions: Optional[Dict] = None,
        confidence: float = 0.5,
        source: str = "auto",
        related_symbols: Optional[List[str]] = None,
    ) -> StrategicMemory:
        """
        存储新的战略观察

        Args:
            memory_type: 记忆类型
            market_context: 市场环境描述
            observation: 观察到的现象
            lesson: 经验教训
            applicability_conditions: 适用条件
            confidence: 初始置信度
            source: 来源
            related_symbols: 关联交易对

        Returns:
            StrategicMemory 实例
        """
        memory = StrategicMemory(
            memory_type=memory_type,
            market_context=market_context,
            observation=observation,
            lesson=lesson,
            applicability_conditions=applicability_conditions or {},
            confidence=confidence,
            source=source,
            related_symbols=related_symbols or [],
            created_at=datetime.utcnow(),
        )

        # 写入缓存
        cache_key = f"{memory_type}:{datetime.utcnow().isoformat()}"
        self._cache.put(cache_key, memory)

        # 持久化到数据库
        self._persist_memory(memory)

        logger.info(f"[StrategicMemory] 存储新记忆: type={memory_type}, lesson={lesson[:80]}...")
        return memory

    def retrieve_relevant(
        self,
        context: str,
        memory_type: Optional[str] = None,
        top_k: int = 5,
    ) -> List[StrategicMemory]:
        """
        检索相关的战略记忆

        Args:
            context: 当前上下文描述（用于关键词匹配）
            memory_type: 过滤特定记忆类型
            top_k: 返回最多 top_k 条

        Returns:
            相关记忆列表，按相关性和置信度排序
        """
        # 先从缓存中查找
        cached = self._cache.values()

        # 从数据库加载
        db_memories = self._load_from_db(memory_type=memory_type, limit=100)

        # 合并去重
        all_memories = list(cached)
        cached_ids = {m.id for m in cached if m.id is not None}
        for m in db_memories:
            if m.id not in cached_ids:
                all_memories.append(m)

        # 关键词匹配评分
        context_lower = context.lower()
        scored = []
        for memory in all_memories:
            score = self._relevance_score(memory, context_lower)
            if score > 0:
                scored.append((memory, score))

        # 按相关性排序
        scored.sort(key=lambda x: x[1], reverse=True)

        return [m for m, _ in scored[:top_k]]

    def validate_memory(self, memory_id: int, outcome: bool) -> None:
        """
        验证记忆

        Args:
            memory_id: 记忆 ID
            outcome: True=验证成功，False=证伪
        """
        self._update_validation(memory_id, outcome)

        if outcome:
            logger.info(f"[StrategicMemory] 记忆 #{memory_id} 验证成功")
        else:
            logger.info(f"[StrategicMemory] 记忆 #{memory_id} 被证伪")

    def extract_rules(self) -> List[Dict]:
        """
        从高验证率的记忆中提取规则

        Returns:
            提取的规则列表
        """
        # 查询符合阈值的记忆
        memories = self._load_validated_memories(
            min_validations=self.RULE_VALIDATION_THRESHOLD,
            min_confidence=self.RULE_CONFIDENCE_THRESHOLD,
        )

        new_rules = []
        for memory in memories:
            rule = {
                "source_memory_id": memory.id,
                "memory_type": memory.memory_type,
                "condition": memory.applicability_conditions,
                "observation": memory.observation,
                "lesson": memory.lesson,
                "confidence": memory.confidence,
                "validation_rate": (
                    memory.times_validated /
                    max(memory.times_validated + memory.times_invalidated, 1)
                ),
            }

            # 检查是否已存在相同规则
            is_duplicate = any(
                r["source_memory_id"] == memory.id for r in self._extracted_rules
            )
            if not is_duplicate:
                new_rules.append(rule)
                self._extracted_rules.append(rule)

        if new_rules:
            logger.info(f"[StrategicMemory] 提取了 {len(new_rules)} 条新规则")

        return new_rules

    def inject_into_prompt(self, context: str, top_k: int = 3) -> str:
        """
        生成可注入 LLM prompt 的文本

        Args:
            context: 当前上下文
            top_k: 返回 top_k 条相关记忆

        Returns:
            格式化的记忆文本
        """
        memories = self.retrieve_relevant(context, top_k=top_k)

        if not memories:
            return ""

        lines = ["历史战略经验："]
        for i, m in enumerate(memories, 1):
            validation_rate = (
                m.times_validated / max(m.times_validated + m.times_invalidated, 1)
            )
            lines.append(
                f"{i}. [{m.memory_type}] {m.lesson} "
                f"(置信度: {m.confidence:.0%}, 验证率: {validation_rate:.0%})"
            )

        # 添加已提取的规则
        if self._extracted_rules:
            lines.append("\n已验证规则：")
            for i, rule in enumerate(self._extracted_rules[-3:], 1):
                lines.append(
                    f"{i}. {rule['lesson']} "
                    f"(验证率: {rule['validation_rate']:.0%})"
                )

        return "\n".join(lines)

    def get_extracted_rules(self) -> List[Dict]:
        """获取已提取的规则列表"""
        return list(self._extracted_rules)

    # -----------------------------------------------------------------------
    # 内部方法
    # -----------------------------------------------------------------------

    def _relevance_score(self, memory: StrategicMemory, context_lower: str) -> float:
        """计算记忆与上下文的相关性评分"""
        score = 0.0

        # 关键词匹配（observation + lesson + market_context）
        text = f"{memory.observation} {memory.lesson} {memory.market_context}".lower()
        context_words = set(context_lower.split())
        matches = sum(1 for w in context_words if w in text and len(w) > 1)
        if context_words:
            score += matches / len(context_words) * 0.5

        # 置信度加权
        score += memory.confidence * 0.3

        # 验证率加权
        total = memory.times_validated + memory.times_invalidated
        if total > 0:
            validation_rate = memory.times_validated / total
            score += validation_rate * 0.2
        else:
            score += 0.1  # 未验证的记忆给基础分

        # 时间衰减（越新越相关）
        if memory.created_at:
            age_hours = (datetime.utcnow() - memory.created_at).total_seconds() / 3600
            if age_hours < 24:
                score += 0.1
            elif age_hours < 168:  # 一周内
                score += 0.05

        return score

    # -----------------------------------------------------------------------
    # 数据库操作
    # -----------------------------------------------------------------------

    def _get_session(self):
        """获取数据库 session"""
        if self._db_session_factory:
            return self._db_session_factory()
        return None

    def _persist_memory(self, memory: StrategicMemory) -> None:
        """持久化记忆到数据库"""
        session = None
        try:
            from .db_models import StrategicMemoryRecord
            session = self._get_session()
            if session is None:
                return

            record = StrategicMemoryRecord(
                memory_type=memory.memory_type,
                market_context=memory.market_context,
                observation=memory.observation,
                lesson=memory.lesson,
                applicability_conditions=json.dumps(memory.applicability_conditions),
                confidence=memory.confidence,
                times_validated=memory.times_validated,
                times_invalidated=memory.times_invalidated,
                source=memory.source,
                related_symbols=json.dumps(memory.related_symbols),
            )
            session.add(record)
            session.commit()
            memory.id = record.id
            session.close()
        except Exception as e:
            logger.warning(f"[StrategicMemory] 持久化失败: {e}")
            if session:
                try:
                    session.rollback()
                    session.close()
                except Exception:
                    pass

    def _load_from_db(
        self,
        memory_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[StrategicMemory]:
        """从数据库加载记忆"""
        session = None
        try:
            from .db_models import StrategicMemoryRecord
            session = self._get_session()
            if session is None:
                return []

            query = session.query(StrategicMemoryRecord).filter(
                StrategicMemoryRecord.is_active == "true"
            )
            if memory_type:
                query = query.filter(StrategicMemoryRecord.memory_type == memory_type)

            records = query.order_by(
                StrategicMemoryRecord.timestamp.desc()
            ).limit(limit).all()
            result = [self._record_to_memory(r) for r in records]
            session.close()
            return result
        except Exception as e:
            logger.debug(f"[StrategicMemory] 加载数据库记忆失败: {e}")
            if session:
                try:
                    session.close()
                except Exception:
                    pass
            return []

    def _load_validated_memories(
        self,
        min_validations: int = 5,
        min_confidence: float = 0.7,
    ) -> List[StrategicMemory]:
        """加载已充分验证的记忆"""
        session = None
        try:
            from .db_models import StrategicMemoryRecord
            session = self._get_session()
            if session is None:
                return []

            records = session.query(StrategicMemoryRecord).filter(
                StrategicMemoryRecord.is_active == "true",
                StrategicMemoryRecord.times_validated >= min_validations,
                StrategicMemoryRecord.confidence >= min_confidence,
            ).all()
            result = [self._record_to_memory(r) for r in records]
            session.close()
            return result
        except Exception as e:
            logger.debug(f"[StrategicMemory] 加载验证记忆失败: {e}")
            if session:
                try:
                    session.close()
                except Exception:
                    pass
            return []

    def _update_validation(self, memory_id: int, outcome: bool) -> None:
        """更新记忆的验证状态"""
        session = None
        try:
            from .db_models import StrategicMemoryRecord
            session = self._get_session()
            if session is None:
                return

            record = session.query(StrategicMemoryRecord).get(memory_id)
            if record:
                if outcome:
                    record.times_validated += 1
                else:
                    record.times_invalidated += 1

                # 更新置信度（基于验证率）
                total = record.times_validated + record.times_invalidated
                if total > 0:
                    record.confidence = record.times_validated / total

                record.last_validated_at = datetime.utcnow()
                session.commit()
            session.close()
        except Exception as e:
            logger.warning(f"[StrategicMemory] 更新验证状态失败: {e}")
            if session:
                try:
                    session.rollback()
                    session.close()
                except Exception:
                    pass

    @staticmethod
    def _record_to_memory(record) -> StrategicMemory:
        """将数据库记录转为 StrategicMemory"""
        return StrategicMemory(
            id=record.id,
            memory_type=record.memory_type,
            market_context=record.market_context or "",
            observation=record.observation or "",
            lesson=record.lesson or "",
            applicability_conditions=(
                json.loads(record.applicability_conditions)
                if record.applicability_conditions else {}
            ),
            confidence=record.confidence or 0.5,
            times_validated=record.times_validated or 0,
            times_invalidated=record.times_invalidated or 0,
            source=record.source or "auto",
            related_symbols=(
                json.loads(record.related_symbols)
                if record.related_symbols else []
            ),
            created_at=record.timestamp,
        )
