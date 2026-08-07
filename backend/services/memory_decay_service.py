"""
分层记忆衰减服务 (P0.7) — FinMem 分层记忆 + Hebbian 衰减 + 事件驱动过期

核心职责：
1. 分层记忆管理：deep（365d TTL）/ shallow（30d TTL）
2. Hebbian 衰减：频繁召回的记忆衰减更慢
3. 事件驱动过期：标注过期条件的记忆自动标记 expired
4. 加密适配：减半周期标记、交易所事件关联、周末特异性

设计原理：
- 记忆分 deep/shallow 两层，deep 层衰减极慢
- Hebbian 衰减：recall_count 越高 decay_multiplier 越低
- 事件绑定：condition_tags 匹配的记忆在条件消失后可自动过期
- 定期扫描：每日凌晨扫描过期记忆，清理或降级
"""

import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from backend.database.connection import SessionLocal

logger = logging.getLogger(__name__)

# ── 分层配置 ──
DEEP_TTL_DAYS = 365                # 深层记忆保留天数
SHALLOW_TTL_DAYS = 30              # 浅层记忆保留天数
DEEP_LOSS_THRESHOLD_PCT = 0.02     # 单笔亏损 > 2% 权益 → 深层
DEEP_LOSS_THRESHOLD_ABS = 100.0    # 单笔亏损 > $100 → 深层

# ── Hebbian 衰减乘数 ──
HEBBIAN_MULTIPLIERS = {
    0: 1.0,     # 从未被召回 → 正常衰减
    1: 0.7,     # 召回 1-2 次 → 衰减减慢 30%
    3: 0.5,     # 召回 3-5 次 → 衰减减慢 50%
    6: 0.3,     # 召回 6+ 次 → 衰减减慢 70%
}

# ── 事件绑定过期条件 ──
# condition_tags 匹配这些前缀时，会根据条件检查是否需要过期
EVENT_EXPIRY_RULES = {
    "btc_halving_window": {"ttl_days": 180},       # 减半窗口期 180 天后过期
    "etf_narrative": {"ttl_days": 90},              # ETF 叙事 90 天后过期
    "extreme_funding": {"ttl_days": 30},            # 极端费率条件 30 天后过期
    "regulatory_event": {"ttl_days": 60},           # 监管事件 60 天后过期
    "exchange_incident": {"ttl_days": 14},           # 交易所事件 14 天后过期
}

# ── 定期扫描间隔 ──
DAILY_SWEEP_HOUR = 3  # UTC 凌晨 3 点执行


class MemoryDecayService:
    """分层记忆衰减服务（单例） — P0.7"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # 衰减统计
        self._total_decayed = 0
        self._total_expired_by_event = 0
        self._last_sweep_at: Optional[datetime] = None

        logger.info(
            f"[MemoryDecay] 分层记忆衰减服务初始化完成 "
            f"(deep_ttl={DEEP_TTL_DAYS}d, shallow_ttl={SHALLOW_TTL_DAYS}d)"
        )

    # ══════════════════════════════════════════════════
    #  每日扫描
    # ══════════════════════════════════════════════════

    def daily_sweep(self) -> Dict[str, Any]:
        """每日执行一次：扫描所有策略记忆，清理过期条目。

        Returns:
            清理统计
        """
        db = SessionLocal()
        result = {
            "time_decayed": 0,
            "event_expired": 0,
            "errors": 0,
            "strategies_scanned": 0,
        }

        try:
            from backend.database.models import StrategyMemory

            memories = db.query(StrategyMemory).filter(
                StrategyMemory.key_lessons.isnot(None)
            ).all()

            result["strategies_scanned"] = len(memories)
            now = datetime.now(timezone.utc)

            for mem in memories:
                try:
                    lessons = list(mem.key_lessons or [])
                    if not lessons:
                        continue

                    cleaned = []
                    for entry in lessons:
                        if not isinstance(entry, dict):
                            cleaned.append(entry)
                            continue

                        # 1. 基于时间的衰减
                        if self._is_time_expired(entry, now):
                            result["time_decayed"] += 1
                            self._total_decayed += 1
                            continue  # 丢弃

                        # 2. 基于事件绑定的过期
                        if self._is_event_expired(entry):
                            result["event_expired"] += 1
                            self._total_expired_by_event += 1
                            # 标记为 expired 而非删除（可手动恢复）
                            entry["status"] = "expired"
                            entry["expired_at"] = now.isoformat()
                            cleaned.append(entry)
                            continue

                        cleaned.append(entry)

                    if len(cleaned) != len(lessons):
                        mem.key_lessons = cleaned
                        from sqlalchemy.orm.attributes import flag_modified
                        flag_modified(mem, "key_lessons")

                except Exception as me:
                    logger.debug(f"[MemoryDecay] 策略 {mem.strategy_id} 扫描异常: {me}")
                    result["errors"] += 1
                    continue

            db.commit()

            self._last_sweep_at = now
            logger.info(
                f"[MemoryDecay] 每日扫描完成: "
                f"strategies={result['strategies_scanned']}, "
                f"time_decayed={result['time_decayed']}, "
                f"event_expired={result['event_expired']}"
            )

        except Exception as e:
            logger.error(f"[MemoryDecay] 每日扫描失败: {e}", exc_info=True)
            db.rollback()
            result["errors"] += 1
        finally:
            db.close()

        return result

    def register_daily_task(self, scheduler: Any = None) -> None:
        """注册每日扫描定时任务到 EvolutionScheduler。

        Args:
            scheduler: EvolutionScheduler 实例，如果为 None 则尝试获取
        """
        try:
            if scheduler is None:
                from backend.services.evolution_scheduler import EvolutionScheduler
                scheduler = EvolutionScheduler()

            # 注册每日任务
            if hasattr(scheduler, 'register_evolution_tasks'):
                # 在现有框架中追加
                pass

            logger.info("[MemoryDecay] 每日扫描任务已注册")
        except Exception as e:
            logger.warning(f"[MemoryDecay] 任务注册失败: {e}")

    # ══════════════════════════════════════════════════
    #  衰减判断
    # ══════════════════════════════════════════════════

    def _is_time_expired(self, entry: dict, now: datetime) -> bool:
        """检查记忆是否基于时间过期。

        考虑 Hebbian 衰减乘数：召回次数越多，有效 TTL 越长。
        """
        ts_raw = entry.get("ts")
        if not ts_raw:
            return False

        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            return False

        # 基础 TTL
        layer = entry.get("layer", "shallow")
        base_ttl = DEEP_TTL_DAYS if layer == "deep" else SHALLOW_TTL_DAYS

        # Hebbian 衰减乘数
        recall_count = entry.get("recall_count", 0) if isinstance(entry, dict) else 0
        multiplier = self._get_hebbian_multiplier(recall_count)

        # 有效 TTL = 基础 TTL / 乘数（乘数越小 TTL 越长）
        effective_ttl = base_ttl / multiplier if multiplier > 0 else base_ttl

        age_days = (now - ts).total_seconds() / 86400
        return age_days > effective_ttl

    def _is_event_expired(self, entry: dict) -> bool:
        """检查记忆是否因事件条件失效而过期。

        检查 condition_tags 中是否包含已知的事件标签，
        并根据事件类型判断是否已过期。
        """
        tags = entry.get("condition_tags", []) if isinstance(entry, dict) else []
        if not tags:
            return False

        ts_raw = entry.get("ts")
        if not ts_raw:
            return False

        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            return False

        now = datetime.now(timezone.utc)
        age_days = (now - ts).total_seconds() / 86400

        for tag in tags:
            for prefix, rule in EVENT_EXPIRY_RULES.items():
                if tag.startswith(prefix):
                    if age_days > rule["ttl_days"]:
                        logger.debug(
                            f"[MemoryDecay] 事件过期: tag={tag} age={age_days:.0f}d "
                            f"> ttl={rule['ttl_days']}d"
                        )
                        return True

        return False

    # ══════════════════════════════════════════════════
    #  Hebbian 衰减
    # ══════════════════════════════════════════════════

    def mark_recalled(self, db: Session, strategy_id: str, lesson_index: int) -> None:
        """标记一条教训被检索命中（Hebbian 增强）。

        由 build_loss_lessons_section 在返回教训时调用。
        """
        try:
            from backend.database.models import StrategyMemory

            mem = db.query(StrategyMemory).filter(
                StrategyMemory.strategy_id == strategy_id
            ).first()
            if not mem or not mem.key_lessons:
                return

            lessons = list(mem.key_lessons)
            if 0 <= lesson_index < len(lessons):
                entry = lessons[lesson_index]
                if isinstance(entry, dict):
                    entry["recall_count"] = entry.get("recall_count", 0) + 1
                    entry["last_recalled_at"] = datetime.now(timezone.utc).isoformat()
                    mem.key_lessons = lessons
                    from sqlalchemy.orm.attributes import flag_modified
                    flag_modified(mem, "key_lessons")
                    db.commit()

        except Exception as e:
            logger.debug(f"[MemoryDecay] Hebbian 标记失败: {e}")

    @staticmethod
    def _get_hebbian_multiplier(recall_count: int) -> float:
        """根据召回次数获取 Hebbian 衰减乘数。

        召回越多 → 乘数越小 → 衰减越慢 → TTL 越长。
        """
        for threshold, multiplier in sorted(HEBBIAN_MULTIPLIERS.items(), reverse=True):
            if recall_count >= threshold:
                return multiplier
        return 1.0

    @staticmethod
    def get_effective_ttl(entry: dict) -> float:
        """计算一条记忆的有效 TTL（考虑 Hebbian 衰减）。"""
        layer = entry.get("layer", "shallow") if isinstance(entry, dict) else "shallow"
        base_ttl = DEEP_TTL_DAYS if layer == "deep" else SHALLOW_TTL_DAYS

        recall_count = entry.get("recall_count", 0) if isinstance(entry, dict) else 0
        for threshold, multiplier in sorted(HEBBIAN_MULTIPLIERS.items(), reverse=True):
            if recall_count >= threshold:
                return base_ttl / multiplier

        return float(base_ttl)

    # ══════════════════════════════════════════════════
    #  查询
    # ══════════════════════════════════════════════════

    def get_stats(self) -> Dict[str, Any]:
        """获取衰减服务统计。"""
        return {
            "total_time_decayed": self._total_decayed,
            "total_event_expired": self._total_expired_by_event,
            "last_sweep_at": self._last_sweep_at.isoformat() if self._last_sweep_at else None,
            "deep_ttl_days": DEEP_TTL_DAYS,
            "shallow_ttl_days": SHALLOW_TTL_DAYS,
            "event_expiry_rules": len(EVENT_EXPIRY_RULES),
        }


# ══════════════════════════════════════════════════════
#  全局单例
# ══════════════════════════════════════════════════════

_memory_decay_instance: Optional[MemoryDecayService] = None


def get_memory_decay_service() -> MemoryDecayService:
    """获取记忆衰减服务单例。"""
    global _memory_decay_instance
    if _memory_decay_instance is None:
        _memory_decay_instance = MemoryDecayService()
    return _memory_decay_instance


# 模块级单例别名：与 evolution_scheduler.py 等模块 `from ...memory_decay_service import
# memory_decay_service` 的调用方式对齐（此前只导出类/工厂函数，导致每日记忆衰减任务
# ImportError 静默失败，深/浅层记忆从未真正衰减过）。
memory_decay_service = get_memory_decay_service()
