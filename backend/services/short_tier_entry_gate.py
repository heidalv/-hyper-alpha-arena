"""short / intraday / scalp 开仓硬门槛。

基于真实盈亏归因：短线 tier 胜率低、累计亏损，需提高出手门槛并限制连续同向开仓。
Fix 7: 新增币种累计亏损熔断（DB 证实 ASTER 10笔赢3亏1万仍被开仓）。
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SHORT_NATURES = {"scalp", "intraday"}
SHORT_TIERS = {"short"}

# 进程内连续同向短线开仓追踪
_same_dir_short_opens: Dict[str, List[float]] = {}

# Fix 7: 币种累计亏损熔断追踪
# key = symbol, value = {"consec_losses": int, "last_loss_at": float, "banned_until": float}
_symbol_loss_tracker: Dict[str, dict] = {}

# 熔断阈值：连续亏损 N 笔 → 进入冷却
CIRCUIT_BREAKER_CONSEC_LOSSES = 4
# 熔断冷却时长（秒）：默认 6 小时
CIRCUIT_BREAKER_COOLDOWN_S = 6 * 3600

# ── 2026-07-06 整改（审查报告 4.6/发现C）──────────────────────────────────
# 此前 _symbol_loss_tracker 是纯进程内存字典：后端热更新/崩溃重启后所有连续
# 亏损计数和冷却窗口清零，被熔断的币种立即"解禁"，与 2026-04-25 日亏熔断
# "状态仅内存、重启丢失"是同一类问题的复发。改为落盘到 data/ 下的 JSON 文件
# （沿用项目里 runtime_tuning_store.py 已有的"轻量 JSON 状态文件"惯例，不引入
# 新的数据库表/迁移），启动时加载、每次状态变化后立即落盘。
# 说明：这解决了"单进程重启丢失"的主要威胁；如果部署时用多个独立 worker 进程
# 且共享同一份磁盘（常见单机多进程场景），写操作各自读改写整份 JSON 仍可能有
# 极小概率的竞态覆盖窗口——比纯内存方案（保证 100% 不同步）好得多，但不是
# 完整的跨进程强一致方案；如未来引入多机部署，应升级为数据库行级持久化。
_CIRCUIT_STATE_FILE = os.path.join("data", "short_tier_circuit_state.json")
_circuit_state_loaded = False


def _load_circuit_state() -> None:
    """启动/首次调用时从磁盘加载熔断状态，只做一次。"""
    global _circuit_state_loaded
    if _circuit_state_loaded:
        return
    _circuit_state_loaded = True
    try:
        if os.path.exists(_CIRCUIT_STATE_FILE):
            with open(_CIRCUIT_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                _symbol_loss_tracker.update(data)
                logger.info(
                    "[ShortTierGate] 已从 %s 加载熔断状态：%d 个币种",
                    _CIRCUIT_STATE_FILE, len(data),
                )
    except Exception as exc:
        logger.warning("[ShortTierGate] 熔断状态加载失败（按空状态启动）: %s", exc)


def _save_circuit_state() -> None:
    """状态变化后立即落盘；写失败只记日志，不影响主流程（熔断判断仍以内存为准）。"""
    try:
        os.makedirs(os.path.dirname(_CIRCUIT_STATE_FILE), exist_ok=True)
        _tmp_path = _CIRCUIT_STATE_FILE + ".tmp"
        with open(_tmp_path, "w", encoding="utf-8") as f:
            json.dump(_symbol_loss_tracker, f, ensure_ascii=False, indent=2)
        os.replace(_tmp_path, _CIRCUIT_STATE_FILE)
    except Exception as exc:
        logger.warning("[ShortTierGate] 熔断状态落盘失败: %s", exc)


_load_circuit_state()


def record_short_tier_open(account_id: int, symbol: str, side: str) -> None:
    """开仓成功后记录，供连续同向检测。"""
    key = _gate_key(account_id, symbol, side)
    _same_dir_short_opens.setdefault(key, []).append(time.time())
    # 只保留最近 2 小时
    cutoff = time.time() - 7200
    _same_dir_short_opens[key] = [t for t in _same_dir_short_opens[key] if t >= cutoff]


def record_short_tier_outcome(symbol: str, pnl: float) -> None:
    """平仓后记录盈亏，更新熔断追踪器。

    由 unified_learning_service.process_outcome 在短线平仓时调用。
    连续亏损达阈值 → 自动熔断该币种一段时间。
    """
    sym = (symbol or "").upper()
    if not sym:
        return
    tracker = _symbol_loss_tracker.setdefault(sym, {"consec_losses": 0, "last_loss_at": 0, "banned_until": 0})
    now = time.time()

    # 清除过期熔断
    if tracker.get("banned_until", 0) and now > tracker["banned_until"]:
        tracker["banned_until"] = 0
        tracker["consec_losses"] = 0

    if pnl < 0:
        tracker["consec_losses"] = tracker.get("consec_losses", 0) + 1
        tracker["last_loss_at"] = now
        if tracker["consec_losses"] >= CIRCUIT_BREAKER_CONSEC_LOSSES:
            tracker["banned_until"] = now + CIRCUIT_BREAKER_COOLDOWN_S
            logger.warning(
                "[ShortTierGate] 🚫 币种熔断: %s 连续亏损 %d 笔，冷却 %dh",
                sym, tracker["consec_losses"], CIRCUIT_BREAKER_COOLDOWN_S // 3600,
            )
    else:
        # 盈利则重置连续亏损计数（但不清除熔断——熔断期内盈利也不解禁）
        tracker["consec_losses"] = 0
    _save_circuit_state()


@dataclass
class ShortTierGateResult:
    allowed: bool
    reason: str = ""
    adjusted_threshold: int = 0
    blocked_nature: Optional[str] = None


def _gate_key(account_id: int, symbol: str, side: str) -> str:
    return f"{account_id}:{symbol.upper()}:{side.lower()}"


def check_short_tier_entry(
    *,
    account_id: int,
    symbol: str,
    side: str,
    action: str,
    confidence: float,
    tier: str,
    trade_nature: str,
    base_entry_threshold: int = 50,
    mode: str = "paper",
) -> ShortTierGateResult:
    """检查 short/scalp/intraday 新开仓是否满足硬门槛。"""
    action_l = (action or "").lower()
    if action_l not in ("buy", "sell", "pyramid", "dca"):
        return ShortTierGateResult(allowed=True)

    tier_l = (tier or "mid").lower()
    nature_l = (trade_nature or "swing").lower()

    from backend.config.settings import (
        SHORT_TIER_CONFIDENCE_EXTRA,
        SHORT_TIER_DISABLED_NATURES,
        SHORT_TIER_SAME_DIR_COOLDOWN_PAPER_S,
        SHORT_TIER_SAME_DIR_COOLDOWN_S,
        SHORT_TIER_SKIP_CONFIDENCE,
    )

    is_short_like = tier_l in SHORT_TIERS or nature_l in SHORT_NATURES
    if not is_short_like:
        return ShortTierGateResult(allowed=True)

    # ScalpRouter 因子分通常 25–45，与 AI 决策 50+ 不是同一标尺
    if nature_l == "scalp":
        from backend.config.settings import SCALP_FACTOR_CONFIRM_THRESHOLD, PAPER_FAST_TRIAL
        base_entry_threshold = int(SCALP_FACTOR_CONFIRM_THRESHOLD or 25)
        extra = 0 if PAPER_FAST_TRIAL else int(SHORT_TIER_CONFIDENCE_EXTRA or 0)
    else:
        extra = int(SHORT_TIER_CONFIDENCE_EXTRA or 8)

    disabled = {n.strip().lower() for n in (SHORT_TIER_DISABLED_NATURES or "").split(",") if n.strip()}
    if nature_l in disabled:
        return ShortTierGateResult(
            allowed=False,
            reason=f"trade_nature={nature_l} 已被绩效归因禁用",
            blocked_nature=nature_l,
        )

    # Fix 7: 币种累计亏损熔断检查
    sym_upper = (symbol or "").upper()
    tracker = _symbol_loss_tracker.get(sym_upper)
    if tracker:
        now_cb = time.time()
        banned_until = tracker.get("banned_until", 0)
        if banned_until and now_cb < banned_until:
            remain_min = int((banned_until - now_cb) / 60)
            consec = tracker.get("consec_losses", 0)
            return ShortTierGateResult(
                allowed=False,
                reason=f"币种 {sym_upper} 连续亏损 {consec} 笔已熔断，剩余冷却 {remain_min}min",
            )

    adjusted = min(90, base_entry_threshold + extra)
    conf = float(confidence or 0)
    # V5 已判置信度；默认跳过 short_tier 二次 conf，只留熔断+同向冷却
    if not SHORT_TIER_SKIP_CONFIDENCE and conf < adjusted:
        return ShortTierGateResult(
            allowed=False,
            reason=(
                f"short/scalp 置信度 {conf:.0f}% < 硬门槛 {adjusted}% "
                f"(基础{base_entry_threshold}%+{extra}%)"
            ),
            adjusted_threshold=adjusted,
        )

    # 连续同向短线开仓冷却（Paper 样本期默认更短）
    key = _gate_key(account_id, symbol, side)
    recent = _same_dir_short_opens.get(key, [])
    _is_paper = (mode or "paper").strip().lower() == "paper"
    cooldown = int(
        SHORT_TIER_SAME_DIR_COOLDOWN_PAPER_S if _is_paper else SHORT_TIER_SAME_DIR_COOLDOWN_S
        or 1800
    )
    now = time.time()
    if recent and (now - recent[-1]) < cooldown:
        return ShortTierGateResult(
            allowed=False,
            reason=(
                f"连续同向短线开仓冷却中 "
                f"({int(now - recent[-1])}s < {cooldown}s)"
            ),
            adjusted_threshold=adjusted,
        )

    return ShortTierGateResult(allowed=True, adjusted_threshold=adjusted)


def apply_short_tier_gate(
    account_id: int,
    symbol: str,
    side: str,
    action: str,
    confidence: float,
    tier: str,
    trade_nature: str,
    base_entry_threshold: int = 50,
) -> Tuple[bool, str]:
    """便捷接口：返回 (allowed, reason)。"""
    result = check_short_tier_entry(
        account_id=account_id,
        symbol=symbol,
        side=side,
        action=action,
        confidence=confidence,
        tier=tier,
        trade_nature=trade_nature,
        base_entry_threshold=base_entry_threshold,
    )
    if not result.allowed:
        logger.info(
            "[ShortTierGate] BLOCK %s %s %s tier=%s nature=%s: %s",
            symbol, side, action, tier, trade_nature, result.reason,
        )
    return result.allowed, result.reason
