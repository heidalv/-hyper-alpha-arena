"""
Paper 节奏控制器 — turbo / warm / balanced / conservative 四档

联动 tick 间隔、策略截流、学习触发频率；读 StrategyRuntimeReport 自动升降档。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

GEARS = ("blitz", "turbo", "warm", "balanced", "conservative")


@dataclass
class PaceKnobs:
    tick_seconds: int = 90
    max_strategies_per_tick: int = 8
    max_symbols_per_tick: int = 8
    learning_review_every_n: int = 15
    learning_miner_every_n: int = 25
    hold_timeout_multiplier: float = 1.0
    master_close_mode: str = "shadow"  # shadow | enforce


_GEAR_KNOBS: Dict[str, PaceKnobs] = {
    "blitz": PaceKnobs(
        tick_seconds=30,
        max_strategies_per_tick=12,
        max_symbols_per_tick=12,
        learning_review_every_n=4,
        learning_miner_every_n=8,
        hold_timeout_multiplier=1.8,
        master_close_mode="shadow",
    ),
    "turbo": PaceKnobs(
        tick_seconds=45,
        max_strategies_per_tick=10,
        max_symbols_per_tick=10,
        learning_review_every_n=8,
        learning_miner_every_n=15,
        hold_timeout_multiplier=1.5,
        master_close_mode="shadow",
    ),
    "warm": PaceKnobs(
        tick_seconds=60,
        max_strategies_per_tick=8,
        max_symbols_per_tick=8,
        learning_review_every_n=12,
        learning_miner_every_n=20,
        hold_timeout_multiplier=1.2,
        master_close_mode="shadow",
    ),
    "balanced": PaceKnobs(
        tick_seconds=90,
        max_strategies_per_tick=8,
        max_symbols_per_tick=8,
        learning_review_every_n=15,
        learning_miner_every_n=25,
        hold_timeout_multiplier=1.0,
        master_close_mode="enforce",
    ),
    "conservative": PaceKnobs(
        tick_seconds=120,
        max_strategies_per_tick=6,
        max_symbols_per_tick=6,
        learning_review_every_n=20,
        learning_miner_every_n=30,
        hold_timeout_multiplier=0.85,
        master_close_mode="enforce",
    ),
}


class PaperPaceController:
    _instance: Optional["PaperPaceController"] = None
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
        try:
            from backend.config.settings import PAPER_FAST_TRIAL
            _fast_default = PAPER_FAST_TRIAL
        except Exception:
            _fast_default = False
        default = os.getenv(
            "PAPER_PACE_DEFAULT_GEAR",
            "blitz" if _fast_default else "turbo",
        ).lower()
        self._gear = default if default in _GEAR_KNOBS else "balanced"
        self._manual_lock = False
        self._last_eval_ts = 0.0
        self._eval_interval = int(os.getenv("PAPER_PACE_EVAL_INTERVAL_S", "1800"))
        self._on_gear_change = None  # 档位变化回调（如重注册 FullAuto tick）

    def register_gear_change_callback(self, cb) -> None:
        """注册档位变化回调（FullAuto 用它在 tick_seconds 变化时重注册循环）。"""
        self._on_gear_change = cb

    def _notify_gear_change(self, old: str, new: str) -> None:
        if old == new or not self._on_gear_change:
            return
        try:
            self._on_gear_change(old, new)
        except Exception as err:
            logger.warning("[PaperPace] gear change callback 失败: %s", err)

    @property
    def gear(self) -> str:
        return self._gear

    def get_knobs(self) -> PaceKnobs:
        return _GEAR_KNOBS.get(self._gear, _GEAR_KNOBS["balanced"])

    def get_tick_seconds(self) -> int:
        return self.get_knobs().tick_seconds

    def set_gear(self, gear: str, *, manual: bool = False, reason: str = "") -> str:
        gear = gear.lower()
        if gear not in _GEAR_KNOBS:
            gear = "balanced"
        old = self._gear
        self._gear = gear
        if manual:
            self._manual_lock = True
        logger.info("[PaperPace] %s → %s (%s)", old, gear, reason or "manual")
        # tick_seconds 等旋钮变化后通知 FullAuto 重注册循环，避免旋钮空转
        if _GEAR_KNOBS.get(old, _GEAR_KNOBS["balanced"]).tick_seconds != \
                _GEAR_KNOBS.get(gear, _GEAR_KNOBS["balanced"]).tick_seconds:
            self._notify_gear_change(old, gear)
        return self._gear

    def force_downshift(self, steps: int = 1, reason: str = "", *, floor: Optional[str] = None) -> str:
        if self._manual_lock:
            logger.info("[PaperPace] skip downshift (manual_lock): %s", reason)
            return self._gear
        order = list(GEARS)
        idx = order.index(self._gear) if self._gear in order else 2
        new_idx = min(len(order) - 1, idx + max(1, steps))
        if floor and floor in order:
            new_idx = min(new_idx, order.index(floor))
        return self.set_gear(order[new_idx], reason=reason or "force_downshift")

    def force_upshift(self, steps: int = 1, reason: str = "", *, ceiling: Optional[str] = None) -> str:
        """向更激进档位升档（turbo 方向）。数据向好时由 evaluate_from_reports 调用。"""
        if self._manual_lock:
            logger.info("[PaperPace] skip upshift (manual_lock): %s", reason)
            return self._gear
        order = list(GEARS)
        idx = order.index(self._gear) if self._gear in order else 2
        new_idx = max(0, idx - max(1, steps))
        if ceiling and ceiling in order:
            new_idx = max(new_idx, order.index(ceiling))
        return self.set_gear(order[new_idx], reason=reason or "force_upshift")

    def unlock_manual(self) -> None:
        self._manual_lock = False

    def evaluate_from_reports(self) -> Optional[str]:
        if self._manual_lock:
            return None
        now = time.time()
        if now - self._last_eval_ts < self._eval_interval:
            return None
        self._last_eval_ts = now

        from backend.services.strategy_runtime_report import load_latest_report

        report = load_latest_report("24h", "ai")
        if not report or report.get("total_closed", 0) < 20:
            return None

        win_rate = float(report.get("win_rate") or 0)
        master_ratio = float(report.get("master_close_loss_ratio") or 0)
        total_pnl = float(report.get("total_pnl") or 0)
        breaches = report.get("rule_breaches") or []

        if breaches or master_ratio > 0.60 or win_rate < 0.40:
            try:
                from backend.config.settings import OPENCODE_MAJOR_PACE_FLOOR
                floor = (OPENCODE_MAJOR_PACE_FLOOR or "balanced").strip().lower()
            except Exception:
                floor = "balanced"
            return self.force_downshift(1, reason="srr_auto_downshift", floor=floor)
        # 数据向好 → 全档逐级自动升档（conservative→balanced→warm→turbo），
        # 不再只限 warm→turbo，让好状态下节奏自然加快（受 manual_lock 保护）
        if win_rate > 0.55 and master_ratio < 0.30 and total_pnl > 0 and self._gear != "turbo":
            return self.force_upshift(1, reason="srr_auto_upshift")
        return None

    def blocks_new_opens_symmetric(self) -> bool:
        """shadow 平仓模式下是否同步禁止新开仓（开平对称）。"""
        try:
            from backend.config.settings import PAPER_PACE_SYMMETRIC_CLOSE
            if not PAPER_PACE_SYMMETRIC_CLOSE:
                return False
            return self.get_knobs().master_close_mode == "shadow"
        except Exception:
            return False

    def to_dict(self) -> Dict[str, Any]:
        k = self.get_knobs()
        return {
            "gear": self._gear,
            "manual_lock": self._manual_lock,
            "tick_seconds": k.tick_seconds,
            "max_strategies_per_tick": k.max_strategies_per_tick,
            "max_symbols_per_tick": k.max_symbols_per_tick,
            "learning_review_every_n": k.learning_review_every_n,
            "learning_miner_every_n": k.learning_miner_every_n,
            "hold_timeout_multiplier": k.hold_timeout_multiplier,
            "master_close_mode": k.master_close_mode,
        }


paper_pace_controller = PaperPaceController()
