"""
锁仓强度配置 — 模拟盘 / 实盘独立调节

强度 0–100，映射到具体风控阈值；持久化至 data/lock_strength.json。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CONFIG_FILE = os.path.join("data", "lock_strength.json")
_CACHE_TTL_SEC = 5.0

_lock = threading.Lock()
_cache: Dict[str, Any] = {"ts": 0.0, "state": None}

PRESET_STRENGTHS = [0, 25, 50, 75, 100]
PRESET_LABELS = {
    0: "关闭",
    25: "宽松",
    50: "标准",
    75: "偏紧",
    100: "严格",
}


@dataclass(frozen=True)
class LockStrengthProfile:
    """解析后的有效锁仓配置（供交易引擎读取）。"""

    mode: str
    strength: int
    preset_label: str
    disable_loss_locks: bool
    symbol_daily_loss_pct: float
    global_extreme_drawdown: float
    global_extreme_daily_loss_pct: float
    consecutive_loss_protection: bool
    mental_loss_to_frozen: int
    risk_score_block_threshold: Optional[int]
    paper_risk_gate: bool
    strategy_guard: bool
    ranging_pause: bool


def _lerp(strength: int, lo: float, hi: float) -> float:
    t = max(0, min(100, int(strength))) / 100.0
    return lo + (hi - lo) * t


def _lerp_int(strength: int, lo: int, hi: int) -> int:
    return int(round(_lerp(strength, float(lo), float(hi))))


def _preset_label(strength: int) -> str:
    s = max(0, min(100, int(strength)))
    nearest = min(PRESET_STRENGTHS, key=lambda p: abs(p - s))
    return PRESET_LABELS.get(nearest, f"{s}%")


def _build_profile(mode: str, strength: int) -> LockStrengthProfile:
    mode = (mode or "paper").strip().lower()
    s = max(0, min(100, int(strength)))

    if mode == "paper":
        disable = s < 8
        if disable:
            return LockStrengthProfile(
                mode=mode,
                strength=s,
                preset_label=_preset_label(s),
                disable_loss_locks=True,
                symbol_daily_loss_pct=1.0,
                global_extreme_drawdown=0.99,
                global_extreme_daily_loss_pct=0.99,
                consecutive_loss_protection=False,
                mental_loss_to_frozen=99,
                risk_score_block_threshold=None,
                paper_risk_gate=False,
                strategy_guard=False,
                ranging_pause=False,
            )
        return LockStrengthProfile(
            mode=mode,
            strength=s,
            preset_label=_preset_label(s),
            disable_loss_locks=False,
            symbol_daily_loss_pct=_lerp(s, 0.12, 0.02),
            global_extreme_drawdown=_lerp(s, 0.55, 0.15),
            global_extreme_daily_loss_pct=_lerp(s, 0.18, 0.05),
            consecutive_loss_protection=s >= 35,
            mental_loss_to_frozen=_lerp_int(s, 10, 4),
            risk_score_block_threshold=None if s < 40 else _lerp_int(s, 95, 70),
            paper_risk_gate=s >= 30,
            strategy_guard=s >= 45,
            ranging_pause=s >= 40,
        )

    # live — 最低档仍保留极端安全网，不能完全关闭
    return LockStrengthProfile(
        mode="live",
        strength=s,
        preset_label=_preset_label(s),
        disable_loss_locks=False,
        symbol_daily_loss_pct=_lerp(s, 0.10, 0.02),
        global_extreme_drawdown=_lerp(s, 0.45, 0.12),
        global_extreme_daily_loss_pct=_lerp(s, 0.15, 0.04),
        consecutive_loss_protection=s >= 20,
        mental_loss_to_frozen=_lerp_int(s, 12, 3),
        risk_score_block_threshold=None if s < 25 else _lerp_int(s, 95, 65),
        paper_risk_gate=False,
        strategy_guard=s >= 30,
        ranging_pause=s >= 35,
    )


def _default_state() -> Dict[str, Any]:
    paper_strength = 0
    try:
        from backend.config.settings import PAPER_DISABLE_LOSS_LOCKS
        if not PAPER_DISABLE_LOSS_LOCKS:
            paper_strength = 50
    except Exception:
        pass
    return {
        "paper": {"strength": paper_strength, "updated_at": None},
        "live": {"strength": 50, "updated_at": None},
    }


def _load_state() -> Dict[str, Any]:
    state = _default_state()
    if os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f) or {}
            for mode in ("paper", "live"):
                if mode in raw and isinstance(raw[mode], dict):
                    st = raw[mode].get("strength")
                    if st is not None:
                        state[mode]["strength"] = max(0, min(100, int(st)))
                    state[mode]["updated_at"] = raw[mode].get("updated_at")
        except Exception as err:
            logger.warning("[LockStrength] 读取配置失败: %s", err)
    return state


def _save_state(state: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(CONFIG_FILE) or "data", exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    _cache["ts"] = 0.0


class LockStrengthService:
    def get_state(self) -> Dict[str, Any]:
        with _lock:
            now = time.time()
            if _cache["state"] is not None and now - _cache["ts"] < _CACHE_TTL_SEC:
                return _cache["state"]
            state = _load_state()
            out = {
                "paper": self._pack_mode_state("paper", state),
                "live": self._pack_mode_state("live", state),
                "presets": self.get_preset_catalog(),
            }
            _cache["state"] = out
            _cache["ts"] = now
            return out

    def _pack_mode_state(self, mode: str, state: Dict[str, Any]) -> Dict[str, Any]:
        strength = int(state.get(mode, {}).get("strength", 50))
        profile = _build_profile(mode, strength)
        return {
            "strength": strength,
            "preset_label": profile.preset_label,
            "updated_at": state.get(mode, {}).get("updated_at"),
            "effective": asdict(profile),
        }

    def get_preset_catalog(self) -> List[Dict[str, Any]]:
        catalog = []
        for s in PRESET_STRENGTHS:
            catalog.append({
                "strength": s,
                "label": PRESET_LABELS[s],
                "paper_summary": _summarize_profile(_build_profile("paper", s)),
                "live_summary": _summarize_profile(_build_profile("live", s)),
            })
        return catalog

    def get_profile(self, mode: str) -> LockStrengthProfile:
        state = _load_state()
        mode = (mode or "paper").strip().lower()
        strength = int(state.get(mode, {}).get("strength", 50 if mode == "live" else 0))
        return _build_profile(mode, strength)

    def set_strength(self, mode: str, strength: int) -> Dict[str, Any]:
        mode = (mode or "").strip().lower()
        if mode not in ("paper", "live"):
            raise ValueError(f"invalid mode: {mode}")
        strength = max(0, min(100, int(strength)))
        with _lock:
            state = _load_state()
            state[mode] = {
                "strength": strength,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            _save_state(state)
            _cache["state"] = None
        self._sync_runtime_flags(mode, strength)
        logger.info("[LockStrength] %s strength -> %s (%s)", mode, strength, _preset_label(strength))
        return self.get_state()

    def _sync_runtime_flags(self, mode: str, strength: int) -> None:
        """同步少量 settings 运行时变量，兼容旧代码路径。"""
        try:
            from backend.config import settings as _settings
            profile = _build_profile(mode, strength)
            if mode == "paper":
                _settings.PAPER_DISABLE_LOSS_LOCKS = bool(profile.disable_loss_locks)
                _settings.CONSECUTIVE_LOSS_PROTECTION_ENABLED = bool(
                    profile.consecutive_loss_protection
                )
                _settings.MENTAL_LOSS_TO_FROZEN = int(profile.mental_loss_to_frozen)
        except Exception as err:
            logger.debug("[LockStrength] sync runtime flags: %s", err)

    def profile_to_public(self, profile: LockStrengthProfile) -> Dict[str, Any]:
        return asdict(profile)


def _summarize_profile(p: LockStrengthProfile) -> str:
    if p.disable_loss_locks:
        return "不锁仓，专注训练"
    parts = []
    if p.symbol_daily_loss_pct < 0.5:
        parts.append(f"单币日亏>{p.symbol_daily_loss_pct*100:.0f}%冻结")
    parts.append(f"回撤>{p.global_extreme_drawdown*100:.0f}%防守")
    if p.consecutive_loss_protection:
        parts.append(f"连亏{p.mental_loss_to_frozen}笔冻结")
    if p.risk_score_block_threshold:
        parts.append(f"风险分>{p.risk_score_block_threshold}禁开仓")
    return " · ".join(parts) if parts else "轻度保护"


_service: Optional[LockStrengthService] = None


def get_lock_strength_service() -> LockStrengthService:
    global _service
    if _service is None:
        _service = LockStrengthService()
    return _service
