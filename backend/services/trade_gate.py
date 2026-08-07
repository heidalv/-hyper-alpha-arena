# backend/services/trade_gate.py
"""单一开仓闸(根因:scalp 旁路绕过总控 + 跨线程无锁)。

所有下单路径(主控 + scalp)必经 TradeGate.check。
职责:① 全局 per-(account,symbol) 锁防并发幽灵单;
     ② 方向冲突检查(tier-aware:同 tier 反向拦截,跨 tier 对冲放行);
     ③ 杠杆/TP-SL 单一权威钳制(结合 PositionCoordinator 的统一杠杆)。
"""
from __future__ import annotations
import logging
import threading
from dataclasses import dataclass
from typing import Optional

from .leverage_authority import resolve_leverage, MIN_LEVERAGE
from .tp_sl_authority import resolve_tp_sl_pct, TIER_TO_NATURE

_log = logging.getLogger(__name__)


@dataclass
class GateDecision:
    allowed: bool
    reason: str = ""
    leverage: float = 0.0
    tp_pct: float = 0.0
    sl_pct: float = 0.0


class TradeGate:
    """单一开仓闸。进程内单例。"""

    def __init__(self):
        self._locks: dict[tuple[int, str], threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # 锁为非重入(threading.Lock)。place_order 及其内部调用(close_position/
    # _fill_market_order/_unify_leverage_for_side)不得对同 (account,symbol) 重入
    # place_order,否则死锁。若未来需重入,改用 threading.RLock。
    def _get_lock(self, account_id: int, symbol: str) -> threading.Lock:
        _key = (account_id, symbol)
        with self._locks_guard:
            if _key not in self._locks:
                self._locks[_key] = threading.Lock()
            return self._locks[_key]

    def acquire(self, account_id: int, symbol: str) -> threading.Lock:
        _lk = self._get_lock(account_id, symbol)
        _lk.acquire()
        return _lk

    def release(self, account_id: int, symbol: str) -> None:
        _key = (account_id, symbol)
        with self._locks_guard:
            _lk = self._locks.get(_key)
        if _lk is not None:
            try:
                _lk.release()
            except RuntimeError:
                pass  # 未持有,忽略

    def check(
        self, db, account_id: int, symbol: str, side: str,
        leverage: float, tier: Optional[str],
        mental_cap: Optional[float] = None,
        trade_nature: Optional[str] = None,
    ) -> GateDecision:
        """闸检查:tier-aware 方向冲突 + 杠威(含统一杠杆)。

        调用方应在 place_order 前先 acquire 再 check。

        tier-aware 规则:
        - 若 trade_nature 已知(显式传入或可由 tier 推断):仅对 *同 trade_nature*
          的子仓位做反向检查;跨 tier 反向(scalp long + trend short)视为合法对冲,
          放行。
        - 若 trade_nature 与 tier 均未知(向后兼容):退化为旧行为,即对同 symbol
          任意反向仓位均拦截。
        """
        from .position_coordinator import position_coordinator as _coord

        # 归一化:order side (buy/sell) → position side (long/short),与 DB 存储一致
        _new_pos_side = "long" if side == "buy" else "short"

        # ① PositionCoordinator:统一杠杆 + 净暴露 + 同 tier 反向检查
        _coord_res = _coord.coordinate_open(
            db=db,
            account_id=account_id,
            symbol=symbol,
            side=_new_pos_side,
            order_side=side,
            leverage=leverage,
            tier=tier,
            trade_nature=trade_nature,
        )
        if not _coord_res.allowed:
            return GateDecision(
                allowed=False,
                reason=_coord_res.reason or "coordinator_rejected",
            )

        # ② 方向冲突检查(tier-aware)
        # 推断本次请求的 trade_nature:显式传入优先,否则由 tier 映射,否则 None。
        _req_nature = trade_nature
        if _req_nature is None and tier is not None:
            _req_nature = TIER_TO_NATURE.get(tier)

        try:
            from backend.database.models import PaperPosition
            _existing_all = (
                db.query(PaperPosition)
                .filter(PaperPosition.account_id == account_id)
                .filter(PaperPosition.symbol == symbol)
                .filter(PaperPosition.status == "open")
                .all()
            )
        except Exception as e:
            # 查询失败不阻塞下单(避免闸成为单点故障),仅记日志
            _log.warning("TradeGate existing-position query failed: %s", e)
            _existing_all = []

        for _existing in _existing_all:
            _ex_side = getattr(_existing, "side", None)
            if not _ex_side or _ex_side == _new_pos_side:
                continue  # 同向或无方向,无冲突

            if _req_nature is None:
                # 向后兼容:trade_nature 与 tier 均未知 → 任意反向仓位拦截
                return GateDecision(
                    allowed=False,
                    reason=(
                        f"direction_conflict: existing {_ex_side} vs new "
                        f"{_new_pos_side} (order {side})"
                    ),
                )

            # tier-aware:仅当现有仓位 *同 trade_nature* 时才视为冲突;
            # 跨 tier 反向 = 合法对冲,放行。
            _ex_nature = getattr(_existing, "trade_nature", None) or ""
            if _req_nature and _ex_nature == _req_nature:
                return GateDecision(
                    allowed=False,
                    reason=(
                        f"direction_conflict: existing {_ex_nature} {_ex_side} "
                        f"vs new {_req_nature} {_new_pos_side} (order {side})"
                    ),
                )

        # ③ 杠杆：交易所同币一仓一杠杆。已有仓必须沿用，不得用新算值压低/抬高。
        _resolved = resolve_leverage(
            tier=tier, requested=leverage, mental_cap=mental_cap,
        )
        _has_existing = bool(_coord_res.existing_sub_positions)
        if _has_existing:
            _ex_levs = []
            for _p in _coord_res.existing_sub_positions:
                try:
                    if isinstance(_p, dict):
                        _ex_levs.append(float(_p.get("leverage") or 1.0))
                    else:
                        _ex_levs.append(float(getattr(_p, "leverage", 1.0) or 1.0))
                except Exception:
                    continue
            _adopt = max(_ex_levs) if _ex_levs else float(_coord_res.unified_leverage or _resolved)
            _lev = max(MIN_LEVERAGE, _adopt)
            if abs(_lev - float(leverage or 0)) > 0.01:
                _log.info(
                    "[TradeGate] adopt existing leverage %.1fx→%.1fx (symbol has open legs)",
                    float(leverage or 0), _lev,
                )
        else:
            _lev = max(MIN_LEVERAGE, _resolved)

        # ④ TP/SL 权威(供调用方回填)
        _tp, _sl = resolve_tp_sl_pct(tier=tier)

        return GateDecision(allowed=True, leverage=_lev, tp_pct=_tp, sl_pct=_sl)


# 进程内单例
trade_gate = TradeGate()
