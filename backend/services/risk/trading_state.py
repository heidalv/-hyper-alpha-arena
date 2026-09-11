# -*- coding: utf-8 -*-
"""TradingState —— 全局交易状态机（v3 方向 7，对标 NautilusTrader TradingState）。

三态：
  ACTIVE    正常：允许开仓/加仓/平仓。
  REDUCING  只平不开：市场冲击（闪崩、组合回撤 30%）自动触发，带 TTL 自动过期。
  HALTED    全停：交易所异常 / 人工急停触发；**不自动过期**，必须人工 release。

与旧 `backend/services/exchange/risk_engine.py`（引擎层规格校验，RISK_ENGINE_ENABLED
默认关）的关系：本模块是**权威**状态源，状态变化时会同步写入旧引擎单例，保证两处一致。

持久化：backend/data/risk/trading_state.json（进程重启后状态保留；HALTED 不会因重启消失）。
线程安全：单进程内用 RLock；写文件用 tmp + os.replace 原子替换。

附加字段（RiskEngine 需要的“全局调节量”，同样持久化）：
  position_scale      组合回撤分级得出的仓位缩放（1.0 / 0.5 / 0.0）
  max_leverage        黑天鹅 playbook 可临时压到 1x（None=不限制，交由 TradeGate）
  no_open_windows     事件避险窗口列表 [{until, symbols|null, reason}]（Phase 2 事件策略接入）
  promotion_freeze_until  黑天鹅后 24h 禁止新策略晋升
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RISK_DATA_DIR = os.path.join(_BACKEND_DIR, "data", "risk")
STATE_FILE = os.path.join(RISK_DATA_DIR, "trading_state.json")


class TradingState(str, Enum):
    ACTIVE = "active"
    REDUCING = "reducing"
    HALTED = "halted"

    @property
    def allows_open(self) -> bool:
        return self is TradingState.ACTIVE

    @classmethod
    def parse(cls, value: Any, default: "TradingState" = None) -> "TradingState":
        if isinstance(value, TradingState):
            return value
        try:
            return cls(str(value or "").strip().lower())
        except ValueError:
            return default if default is not None else cls.ACTIVE


# 状态严格序：HALTED > REDUCING > ACTIVE（自动触发只能“升级”，不能把人工 HALTED 降级）
_SEVERITY = {TradingState.ACTIVE: 0, TradingState.REDUCING: 1, TradingState.HALTED: 2}


@dataclass
class StateSnapshot:
    state: str = TradingState.ACTIVE.value
    reason: str = ""
    source: str = ""              # manual / kill_switch / flash_crash / drawdown / connectivity / anomaly_agent
    since: float = 0.0            # epoch
    until: Optional[float] = None  # epoch；None=不过期（HALTED 必须为 None）
    position_scale: float = 1.0
    max_leverage: Optional[float] = None
    no_open_windows: List[Dict[str, Any]] = field(default_factory=list)
    promotion_freeze_until: Optional[float] = None
    history: List[Dict[str, Any]] = field(default_factory=list)  # 最近 50 次状态变更

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["state_enum"] = self.state
        return d


class TradingStateStore:
    """进程内单例 + 文件持久化。所有读取都先做 TTL 过期判定。"""

    def __init__(self, path: str = STATE_FILE):
        self._path = path
        self._lock = threading.RLock()
        self._snap: Optional[StateSnapshot] = None
        self._loaded_at = 0.0

    # ---------- 载入/保存 ----------
    def _load(self, force: bool = False) -> StateSnapshot:
        with self._lock:
            # 文件可能被其它进程/人工改动（例如 kill 文件），5s 内复用内存副本
            if self._snap is not None and not force and time.time() - self._loaded_at < 5.0:
                return self._snap
            snap = StateSnapshot()
            try:
                if os.path.isfile(self._path):
                    with open(self._path, "r", encoding="utf-8") as f:
                        raw = json.load(f) or {}
                    known = {k: v for k, v in raw.items() if k in StateSnapshot.__dataclass_fields__}
                    snap = StateSnapshot(**known)
            except Exception as exc:
                logger.warning("[TradingState] 读取状态文件失败，回退 ACTIVE: %s", exc)
                snap = StateSnapshot()
            self._snap = snap
            self._loaded_at = time.time()
            return snap

    def _save(self, snap: StateSnapshot) -> None:
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self._path), exist_ok=True)
                tmp = self._path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(asdict(snap), f, ensure_ascii=False, indent=2)
                os.replace(tmp, self._path)
            except Exception as exc:
                logger.error("[TradingState] 写状态文件失败: %s", exc)
            self._snap = snap
            self._loaded_at = time.time()

    # ---------- 读取 ----------
    def snapshot(self) -> StateSnapshot:
        """返回已做 TTL 过期处理的快照（过期的 REDUCING 自动回 ACTIVE 并落盘）。"""
        with self._lock:
            snap = self._load()
            changed = False
            now = time.time()
            if snap.until is not None and snap.state != TradingState.HALTED.value and now >= float(snap.until):
                self._push_history(snap, "expired")
                snap.state = TradingState.ACTIVE.value
                snap.reason = f"expired: {snap.reason}"
                snap.source = "ttl"
                snap.since = now
                snap.until = None
                changed = True
            # 事件避险窗口过期清理
            live_windows = [w for w in (snap.no_open_windows or []) if float(w.get("until") or 0) > now]
            if len(live_windows) != len(snap.no_open_windows or []):
                snap.no_open_windows = live_windows
                changed = True
            if snap.promotion_freeze_until is not None and now >= float(snap.promotion_freeze_until):
                snap.promotion_freeze_until = None
                changed = True
            if changed:
                self._save(snap)
                self._sync_legacy_engine(TradingState.parse(snap.state))
            return snap

    def state(self) -> TradingState:
        return TradingState.parse(self.snapshot().state)

    # ---------- 写入 ----------
    def set_state(
        self,
        state: TradingState,
        *,
        reason: str,
        source: str,
        ttl_seconds: Optional[float] = None,
        escalate_only: bool = False,
        position_scale: Optional[float] = None,
        max_leverage: Optional[float] = ...,  # type: ignore[assignment]
    ) -> StateSnapshot:
        """设置状态。

        escalate_only=True（自动触发器专用）：只允许把状态“升级”或续期，不能把更严的
        状态降级——例如人工 HALTED 期间闪崩触发 REDUCING 不会覆盖 HALTED。
        HALTED 忽略 ttl（永不自动过期）。
        """
        state = TradingState.parse(state)
        with self._lock:
            snap = self.snapshot()
            cur = TradingState.parse(snap.state)
            if escalate_only and _SEVERITY[state] < _SEVERITY[cur]:
                logger.info("[TradingState] escalate_only 跳过降级 %s → %s (%s)", cur.value, state.value, reason)
                return snap
            now = time.time()
            if state == TradingState.HALTED:
                until = None
            elif ttl_seconds is not None and ttl_seconds > 0:
                until = now + float(ttl_seconds)
            else:
                until = None
            if cur != state or (snap.reason != reason):
                self._push_history(snap, f"{source}:{reason}")
            snap.state = state.value
            snap.reason = str(reason or "")[:300]
            snap.source = str(source or "")[:40]
            snap.since = now
            snap.until = until
            if position_scale is not None:
                snap.position_scale = max(0.0, min(1.0, float(position_scale)))
            if max_leverage is not ...:
                snap.max_leverage = (float(max_leverage) if max_leverage is not None else None)
            if state == TradingState.ACTIVE and position_scale is None:
                # 回到 ACTIVE 且未显式给缩放 → 恢复 1.0（回撤分级会在下一 tick 重新评估）
                snap.position_scale = 1.0
            self._save(snap)
            self._sync_legacy_engine(state)
            logger.warning(
                "[TradingState] %s → %s source=%s reason=%s ttl=%s scale=%.2f",
                cur.value, state.value, source, reason,
                (f"{ttl_seconds:.0f}s" if ttl_seconds else "none"), snap.position_scale,
            )
            return snap

    def set_position_scale(self, scale: float, *, reason: str) -> StateSnapshot:
        with self._lock:
            snap = self.snapshot()
            new_scale = max(0.0, min(1.0, float(scale)))
            if abs(new_scale - float(snap.position_scale)) > 1e-9:
                self._push_history(snap, f"scale:{reason}")
                snap.position_scale = new_scale
                self._save(snap)
                logger.warning("[TradingState] position_scale → %.2f (%s)", new_scale, reason)
            return snap

    def add_no_open_window(self, *, until: float, reason: str, symbols: Optional[List[str]] = None) -> StateSnapshot:
        with self._lock:
            snap = self.snapshot()
            snap.no_open_windows = list(snap.no_open_windows or []) + [{
                "until": float(until), "reason": str(reason)[:200],
                "symbols": [s.upper() for s in symbols] if symbols else None,
                "added_at": time.time(),
            }]
            self._save(snap)
            return snap

    def set_promotion_freeze(self, seconds: float, reason: str) -> StateSnapshot:
        with self._lock:
            snap = self.snapshot()
            snap.promotion_freeze_until = time.time() + float(seconds)
            self._push_history(snap, f"promotion_freeze:{reason}")
            self._save(snap)
            return snap

    def in_no_open_window(self, symbol: Optional[str]) -> Optional[Dict[str, Any]]:
        snap = self.snapshot()
        sym = (symbol or "").upper().split("/")[0]
        for w in snap.no_open_windows or []:
            syms = w.get("symbols")
            if not syms or sym in syms:
                return w
        return None

    # ---------- 内部 ----------
    def _push_history(self, snap: StateSnapshot, note: str) -> None:
        snap.history = (snap.history or [])[-49:] + [{
            "ts": time.time(), "from": snap.state, "reason": snap.reason,
            "source": snap.source, "note": str(note)[:200],
        }]

    @staticmethod
    def _sync_legacy_engine(state: TradingState) -> None:
        """把权威状态同步到旧 exchange.risk_engine 单例（若已启用则其 check_submit 同样拒单）。"""
        try:
            from backend.services.exchange.risk_engine import get_risk_engine, TradingState as _Legacy
            _map = {
                TradingState.ACTIVE: _Legacy.ACTIVE,
                TradingState.REDUCING: _Legacy.REDUCING,
                TradingState.HALTED: _Legacy.HALTED,
            }
            get_risk_engine().set_trading_state(_map[state])
        except Exception as exc:  # pragma: no cover - 旧引擎不可用不影响权威状态
            logger.debug("[TradingState] 同步旧引擎失败: %s", exc)

    def reset_for_tests(self) -> None:
        with self._lock:
            self._snap = None
            self._loaded_at = 0.0
            try:
                if os.path.isfile(self._path):
                    os.remove(self._path)
            except Exception:
                pass


_store: Optional[TradingStateStore] = None
_store_lock = threading.Lock()


def get_state_store() -> TradingStateStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = TradingStateStore()
    return _store
