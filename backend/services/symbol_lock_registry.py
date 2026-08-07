"""SymbolLockRegistry — 统一锁仓注册表（2026-06-19 重构）。

所有锁仓/冻结/暂停入口的唯一真相来源。替代之前分散在 4 种状态存储
（session.pause_reason / _strat_pause_meta / genome.pause_reason / AIStrategy.status）
的 13 个独立锁仓入口。

核心特性：
1. 单一注册表：所有锁/解锁操作走这里，_paper_auto_unlock 按 registry 判断
2. hysteresis（指数退避）：同一 symbol+reason 重复锁定时冷却递增
3. TTL：所有锁有过期时间（除了 manual/crash/training_rebalance）
4. 死锁不再永久 frozen（deadlock 24h TTL）
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 各 reason_code 的基础冷却时间（秒），0 = 需手动解锁
_HYSTERESIS_BASE: Dict[str, int] = {
    "per_symbol_loss": 1800,       # 30min
    "consec_loss": 1800,           # 30min
    "ranging": 600,                # 10min
    "orchestrator_frozen": 1800,   # 30min
    "health_pause": 3600,          # 1h
    "data_stale": 1800,            # 30min
    "champion_pause": 0,           # 需 training 解锁
    "symbol_removed": 0,           # 需重新添加
    "session_paused": 0,           # 需 resume
    "crash": 0,                    # 需 crash 解除
    "training_rebalance": 0,       # 需 rebalance 解锁
    "manual": 0,                   # 需手动解锁
    "deadlock": 86400,             # 24h TTL（之前是永久）
}

# 指数退避上限（最多翻 5 次 = 32x）
_MAX_HYSTERESIS_EXP = 5


@dataclass
class LockRecord:
    """单条锁记录。"""
    symbol: str
    strategy_id: Optional[str]     # None = symbol 级锁
    reason_code: str
    by: str                        # 来源模块
    locked_at: float
    expires_at: float              # 0 = 永不过期
    hysteresis_count: int = 0
    unlock_condition: str = ""

    @property
    def key(self) -> str:
        """唯一键：symbol:strategy_id:reason_code。"""
        return f"{self.symbol}:{self.strategy_id or ''}:{self.reason_code}"

    @property
    def is_expired(self) -> bool:
        if self.expires_at == 0:
            return False
        return time.time() > self.expires_at

    @property
    def remaining_sec(self) -> float:
        if self.expires_at == 0:
            return float('inf')
        return max(0, self.expires_at - time.time())


class SymbolLockRegistry:
    """统一锁仓注册表 — 单例。"""

    _instance: Optional["SymbolLockRegistry"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._locks: Dict[str, LockRecord] = {}
            cls._instance._hysteresis_counts: Dict[str, int] = {}  # key=symbol:reason → count
        return cls._instance

    def lock(
        self,
        symbol: str,
        strategy_id: Optional[str] = None,
        reason_code: str = "manual",
        by: str = "unknown",
        duration_sec: int = 0,
        unlock_condition: str = "",
    ) -> bool:
        """注册一条锁。

        Args:
            symbol: 交易对
            strategy_id: 策略ID（None = symbol 级锁）
            reason_code: 锁定原因（见 _HYSTERESIS_BASE）
            by: 来源模块
            duration_sec: 持续时间（0 = 用 reason_code 的默认值，-1 = 永不过期）
            unlock_condition: 解锁条件描述

        Returns:
            True = 新锁定，False = 已存在或被 paper 模式过滤

        Paper 模式策略（2026-06-19）：
        模拟盘目的是训练 AI，锁仓门槛极低。只有 deadlock/crash/manual/training_rebalance
        这些"系统级"锁仓在 paper 模式生效；其余（per_symbol_loss/consec_loss/ranging/
        health_pause/orchestrator_frozen/champion_pause）在 paper 模式全部跳过。
        """
        # ── Paper 模式极宽松锁仓 ──
        _is_paper = False
        if not globals().get('_FORCE_LIVE_FOR_TESTS', False):
            try:
                from backend.services.lock_strength_service import get_lock_strength_service
                _is_paper = get_lock_strength_service().get_profile("paper").disable_loss_locks
            except Exception:
                pass

        if _is_paper:
            # Paper 模式只允许系统级锁仓，其余全部跳过
            _PAPER_ALLOWED_REASONS = {"deadlock", "crash", "manual", "training_rebalance", "symbol_removed", "session_paused"}
            if reason_code not in _PAPER_ALLOWED_REASONS:
                logger.debug(
                    f"[LockRegistry] PAPER 模式跳过锁仓 {symbol} reason={reason_code} "
                    f"（仅允许 {_PAPER_ALLOWED_REASONS}）"
                )
                return False
        now = time.time()

        # hysteresis: 同一 symbol+reason 的锁定次数
        _hyst_key = f"{symbol}:{reason_code}"
        _count = self._hysteresis_counts.get(_hyst_key, 0)

        # 计算持续时间
        if duration_sec == -1:
            _expires = 0  # 永不过期
        else:
            if duration_sec > 0:
                _base = duration_sec
            else:
                _base = _HYSTERESIS_BASE.get(reason_code, 1800)

            if _base > 0:
                # 指数退避：第 N 次锁定 × 2^min(N, 5)
                _multiplier = 2 ** min(_count, _MAX_HYSTERESIS_EXP)
                _duration = _base * _multiplier
                _expires = now + _duration
            else:
                _expires = 0  # 永不过期

        _key = f"{symbol}:{strategy_id or ''}:{reason_code}"

        # 已存在且未过期 → 不重复锁
        existing = self._locks.get(_key)
        if existing and not existing.is_expired:
            return False

        record = LockRecord(
            symbol=symbol,
            strategy_id=strategy_id,
            reason_code=reason_code,
            by=by,
            locked_at=now,
            expires_at=_expires,
            hysteresis_count=_count,
            unlock_condition=unlock_condition or f"reason={reason_code}",
        )
        self._locks[_key] = record
        self._hysteresis_counts[_hyst_key] = _count + 1

        _remain = "永久" if _expires == 0 else f"{int((_expires - now) / 60)}min"
        logger.info(
            f"[LockRegistry] LOCK {symbol} sid={strategy_id or '-'} "
            f"reason={reason_code} by={by} duration={_remain} "
            f"hysteresis=#{_count}"
        )
        return True

    def unlock(
        self,
        symbol: str,
        strategy_id: Optional[str] = None,
        reason_code: Optional[str] = None,
    ) -> bool:
        """解除锁。

        Args:
            symbol: 交易对
            strategy_id: 策略ID（None = 所有策略级锁 + symbol 级锁）
            reason_code: 指定原因（None = 解除所有原因）

        Returns:
            True = 至少解除了一条
        """
        removed = []
        for key, record in list(self._locks.items()):
            if record.symbol != symbol.upper() and record.symbol != symbol:
                continue
            if strategy_id is not None and record.strategy_id != str(strategy_id):
                continue
            if reason_code is not None and record.reason_code != reason_code:
                continue
            # symbol 级锁（strategy_id=None）：如果调用方没指定 strategy_id，也匹配
            if strategy_id is None and record.strategy_id is not None:
                continue  # 只解 symbol 级锁，不解策略级（除非显式指定）
            removed.append(key)
            del self._locks[key]

        # hysteresis: 不在 unlock 时清（保留累积），只 cleanup_expired 时清
        if removed:
            logger.info(
                f"[LockRegistry] UNLOCK {symbol} sid={strategy_id or '-'} "
                f"reason={reason_code or 'all'} removed={len(removed)}"
            )
        return len(removed) > 0

    def unlock_all(self, symbol: str) -> int:
        """解除某 symbol 的所有锁（symbol 级 + 策略级）。返回解除数。"""
        removed = [k for k, r in self._locks.items() if r.symbol == symbol]
        for k in removed:
            del self._locks[k]
            _parts = k.split(":")
            self._hysteresis_counts.pop(f"{_parts[0]}:{_parts[2]}", None)
        if removed:
            logger.info(f"[LockRegistry] UNLOCK_ALL {symbol} removed={len(removed)}")
        return len(removed)

    def is_locked(self, symbol: str, strategy_id: Optional[str] = None) -> bool:
        """查询是否被锁。"""
        sym = symbol.upper()
        for record in self._locks.values():
            if record.is_expired:
                continue
            if record.symbol != sym:
                continue
            # symbol 级锁（strategy_id=None）：锁整个 symbol
            if record.strategy_id is None:
                return True
            # 策略级锁：精确匹配
            if strategy_id is not None and record.strategy_id == str(strategy_id):
                return True
        return False

    def get_lock_reason(self, symbol: str, strategy_id: Optional[str] = None) -> Optional[str]:
        """获取锁定原因（供日志/前端展示）。"""
        sym = symbol.upper()
        for record in self._locks.values():
            if record.is_expired:
                continue
            if record.symbol != sym:
                continue
            if record.strategy_id is None:
                return record.reason_code
            if strategy_id is not None and record.strategy_id == str(strategy_id):
                return record.reason_code
        return None

    def should_skip_revive(self, symbol: str, strategy_id: str) -> bool:
        """替代 _should_skip_revive：任何有活跃锁的策略都跳过恢复。

        manual / training_rebalance / symbol_removed / session_paused / champion_pause
        即使过期也不自动恢复（需要显式 unlock）。
        """
        sym = symbol.upper()
        for record in self._locks.values():
            if record.symbol != sym:
                continue
            if record.strategy_id is None or record.strategy_id == str(strategy_id):
                reason = record.reason_code
                # 需要显式解锁的 reason：不管过没过期都不自动恢复
                if reason in ("manual", "training_rebalance", "symbol_removed", "session_paused", "champion_pause"):
                    return True
                # 其他锁未过期 → 跳过恢复
                if not record.is_expired:
                    return True
        return False

    def cleanup_expired(self) -> int:
        """清理过期锁。每个 tick 调一次。返回清理数。"""
        now = time.time()
        expired_keys = [
            k for k, r in self._locks.items()
            if r.expires_at > 0 and now > r.expires_at
        ]
        for k in expired_keys:
            record = self._locks[k]
            logger.info(
                f"[LockRegistry] EXPIRED {record.symbol} "
                f"reason={record.reason_code} (locked {int((now - record.locked_at) / 60)}min ago)"
            )
            del self._locks[k]
            _parts = k.split(":")
            self._hysteresis_counts.pop(f"{_parts[0]}:{_parts[2]}", None)
        return len(expired_keys)

    def get_status_summary(self) -> List[Dict]:
        """获取所有活跃锁的摘要（供 API/前端展示）。"""
        now = time.time()
        result = []
        for record in self._locks.values():
            if record.is_expired:
                continue
            result.append({
                "symbol": record.symbol,
                "strategy_id": record.strategy_id,
                "reason": record.reason_code,
                "by": record.by,
                "locked_at": record.locked_at,
                "remaining_min": int(record.remaining_sec / 60) if record.remaining_sec != float('inf') else -1,
                "unlock_condition": record.unlock_condition,
            })
        return result


# 全局单例
lock_registry = SymbolLockRegistry()
