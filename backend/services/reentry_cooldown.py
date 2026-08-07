"""
再开仓冷却（防手续费磨损 + 防方向翻转磨损）

v5 (tier-isolated): 冷却以 (account, symbol, tier) 为独立桶 —
  - 平掉 long-tier 仓位不再阻止 mid-tier 或 short-tier 同 symbol 开仓
  - 避免多周期并行架构下「一次 long 全平冻结整个 symbol 4 小时」
  - 若调用方未传 tier，会回退成 "default" 桶，保持向后兼容

三层冷却机制（每个桶独立）：
  1. 同向冷却：平多后抑制再开多（较长），防止信号未变时刷手续费
  2. 反向冷却：平多后也短暂抑制开空（较短），防止快速多空翻转导致双重损失
  3. 连续亏损累积冷却：同 symbol 连续亏损平仓时冷却翻倍

不同持仓周期使用不同冷却时间：
  同向: short 15min / mid 30min / long 60min
  反向: 统一 30min
  总控全平(master_running): 最低 60min
  连续亏损(>=2笔): 冷却时间翻倍
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

_lock = threading.Lock()
# key: "{account_id}_{symbol}_{tier}" -> (position_side_closed, unix_ts, tier, cooldown_multiplier)
#   v5: tier 进入 key，不同 tier 各自独立冷却，不再互相影响
_state: Dict[str, Tuple[str, float, str, float]] = {}


def _state_key(account_id: int, symbol: str, tier: str) -> str:
    """构建 tier 隔离的状态 key（symbol 统一大写，避免大小写漂移）。"""
    _t = (tier or "").strip().lower() or "default"
    if _t not in ("short", "mid", "long", "default"):
        _t = "default"
    _sym = (symbol or "").strip().upper()
    return f"{account_id}_{_sym}_{_t}"

# 连续亏损历史: key="{account_id}_{symbol}" -> list of (pnl, unix_ts)
_loss_history: Dict[str, List[Tuple[float, float]]] = {}

_FALLBACK_COOLDOWN_SEC = 10 * 60        # 10 分钟
_FLIP_COOLDOWN_SEC = 30 * 60            # 反向翻转冷却：30 分钟
_MASTER_CLOSE_MIN_COOLDOWN = 60 * 60    # 总控全平最低冷却：60 分钟
_LOSS_HISTORY_WINDOW_SEC = 4 * 3600     # 亏损历史统计窗口：4 小时


def _get_cooldown_sec(tier: str) -> int:
    """根据 tier 获取同向冷却时间（秒）"""
    try:
        from backend.config.settings import TIER_PROTECTION_PARAMS
        _tier_cfg = TIER_PROTECTION_PARAMS.get(tier, TIER_PROTECTION_PARAMS["mid"])
        return _tier_cfg["cooldown_sec"]
    except Exception:
        return _FALLBACK_COOLDOWN_SEC


def _get_loss_multiplier(account_id: int, symbol: str) -> float:
    """根据近期连续亏损次数计算冷却倍率"""
    key = f"{account_id}_{(symbol or '').strip().upper()}"
    now = time.time()

    with _lock:
        history = _loss_history.get(key, [])
        # 清理过期记录
        history = [(pnl, ts) for pnl, ts in history if now - ts < _LOSS_HISTORY_WINDOW_SEC]
        _loss_history[key] = history

    if len(history) < 2:
        return 1.0

    # 检查最近 N 笔是否连续亏损
    recent = sorted(history, key=lambda x: -x[1])
    consecutive_losses = 0
    for pnl, _ in recent:
        if pnl < 0:
            consecutive_losses += 1
        else:
            break

    if consecutive_losses >= 3:
        return 3.0
    elif consecutive_losses >= 2:
        return 2.0
    return 1.0


def record_close_pnl(account_id: int, symbol: str, pnl: float) -> None:
    """记录一次平仓的盈亏，用于连续亏损检测。"""
    key = f"{account_id}_{(symbol or '').strip().upper()}"
    now = time.time()
    with _lock:
        if key not in _loss_history:
            _loss_history[key] = []
        _loss_history[key].append((pnl, now))
        # 限制列表大小
        if len(_loss_history[key]) > 20:
            _loss_history[key] = _loss_history[key][-20:]


def record_full_close(
    account_id: int, symbol: str, position_side: str,
    tier: str = "mid",
    is_master_close: bool = False,
    close_pnl: float = 0.0,
    close_reason: str = "",
) -> None:
    """
    记录一次全平。

    Args:
        account_id: 账户 ID
        symbol: 交易对
        position_side: 被平掉的持仓方向（long / short）
        tier: 持仓周期
        is_master_close: 是否为总控全平 (master_running)
        close_pnl: 平仓盈亏
    """
    if not symbol or position_side not in ("long", "short"):
        return

    # 记录盈亏用于连续亏损检测
    if close_pnl != 0:
        record_close_pnl(account_id, symbol, close_pnl)

    _norm_tier = (tier or "").strip().lower() or "mid"
    if _norm_tier not in ("short", "mid", "long"):
        _norm_tier = "mid"
    key = _state_key(account_id, symbol, _norm_tier)
    base_cd = _get_cooldown_sec(_norm_tier)

    # 止盈后最低冷却（修复 tp 后 15 分钟内同向再开）
    _reason_l = (close_reason or "").strip().lower()
    if _reason_l in ("tp", "breakeven_tp", "safety_tp", "tp_target"):
        try:
            from backend.config.settings import REENTRY_MIN_COOLDOWN_AFTER_TP_SEC
            _tp_floor = int(REENTRY_MIN_COOLDOWN_AFTER_TP_SEC or 0)
        except Exception:
            _tp_floor = 1800
        if _tp_floor > 0:
            base_cd = max(base_cd, _tp_floor)

    # ── 补齐修复（04 综合方案 §2.3.5 分层冷却矩阵「任意亏损全平」行）：
    # 此前只对 close_reason∈{sl,liquidation,...} 延长冷却，但实盘里大量真实亏损
    # 是以 master_running_close / dust_cleanup / profit_drawdown_full / hold_timeout_review
    # 等"软"标签平仓的（严格 sl 标签仅占 3.9%）——这些标签之前只吃 30min/4h 的普通
    # base_cd，完全没有被本函数的"亏损后加长冷却"覆盖到，是恶性循环没被打断的
    # 主因之一。现在不再依赖 close_reason 文本，只要 close_pnl<0（任意原因的真实
    # 亏损），就统一套用 mid=4h / long=12h 的下限；sl/liquidation 再在此基础上
    # 进一步加长到 12h/48h（两者取 max，不冲突）。
    if close_pnl < 0:
        try:
            _loss_cd_env = {"mid": "14400", "long": "43200", "short": "14400"}.get(
                _norm_tier, "14400"
            )
            _loss_cd = int(os.getenv(
                f"REENTRY_LOSS_COOLDOWN_SEC_{_norm_tier.upper()}", _loss_cd_env
            ))
            if _loss_cd > 0:
                base_cd = max(base_cd, _loss_cd)
        except Exception:
            pass

    # ── S0-8 止血修复（R1 强化）：sl/liquidation 后冷却大幅延长 ──
    # 审计发现 57.4% 亏损后 24h 同向再开率——窄冷却（mid 30min/long 60min）
    # 不足以打断恶性循环。sl/liquidation 是硬止损事件，冷却应远长于普通平仓。
    # 配置：REENTRY_SL_COOLDOWN_SEC_BY_TIER（env 可覆盖）
    #   mid: 12 小时 / long: 48 小时（对齐 04 综合方案 §2.3.5 分层冷却矩阵）
    if _reason_l in ("sl", "stop_loss", "stop loss", "liquidation", "margin_call"):
        try:
            _sl_cd_env = {
                "mid": "43200",    # 12h
                "long": "172800",  # 48h
                "short": "14400",  # 4h
            }.get(_norm_tier, "14400")
            _sl_cd = int(os.getenv(
                f"REENTRY_SL_COOLDOWN_SEC_{_norm_tier.upper()}", _sl_cd_env
            ))
            if _sl_cd > 0:
                base_cd = max(base_cd, _sl_cd)
        except Exception:
            pass

    # 总控全平 -> 至少 60 分钟冷却
    if is_master_close:
        base_cd = max(base_cd, _MASTER_CLOSE_MIN_COOLDOWN)

    # 连续亏损倍率
    multiplier = _get_loss_multiplier(account_id, symbol)
    # S0-8 修复:把 effective_cd 也存入 state,供 reopen_blocked 读取
    # （之前只存 base_cd 来源,sl/tp/master 延长值在 reopen 时丢失）
    effective_cd = int(base_cd * multiplier)

    with _lock:
        _state[key] = (position_side, time.time(), _norm_tier, multiplier, effective_cd)

    logger.info(
        f"[ReentryCooldown] 记录全平 account={account_id} {symbol} {position_side} "
        f"tier={_norm_tier} master={is_master_close} pnl={close_pnl:+.2f} "
        f"同向{effective_cd//60}分钟(base={base_cd//60}min x{multiplier:.0f}倍)/反向{_FLIP_COOLDOWN_SEC//60}分钟冷却"
        f"（仅影响同 tier 再开仓）"
    )


def reopen_blocked(
    account_id: int, symbol: str, open_action: str,
    new_tier: str = "",
) -> Tuple[bool, str]:
    """
    平仓后冷却检查（v5: 按 tier 隔离）：
    - 同向再开：使用 tier 配置的冷却时间 * 连续亏损倍率
    - 反向翻转：使用 _FLIP_COOLDOWN_SEC
    - new_tier: 即将开仓的 tier（short/mid/long）。**冷却只在新旧 tier 相同时生效**，
      不同 tier 相互独立，避免 long 全平锁死 mid/short 开仓通路。
      若未提供，回退为全局检查（向后兼容）。

    open_action: buy -> 开多 long, sell -> 开空 short
    """
    if open_action == "buy":
        new_position_side = "long"
    elif open_action == "sell":
        new_position_side = "short"
    else:
        return False, ""

    _nt = (new_tier or "").strip().lower()
    if _nt not in ("short", "mid", "long"):
        _nt = ""  # 未指定 tier → 逐桶检查

    # 要检查的 key 列表（v5 tier 隔离）
    _keys_to_check = []
    if _nt:
        _keys_to_check.append(_state_key(account_id, symbol, _nt))
    else:
        # 未指定 tier：检查所有 tier 桶（向后兼容旧调用）
        for _t in ("short", "mid", "long", "default"):
            _keys_to_check.append(_state_key(account_id, symbol, _t))
        # 兼容 v4 旧格式残留 key
        _keys_to_check.append(f"{account_id}_{symbol}")

    data = None
    matched_key = None
    with _lock:
        for k in _keys_to_check:
            _d = _state.get(k)
            if _d:
                data = _d
                matched_key = k
                break
    if not data:
        # 内存无记录时，回查 DB 最近亏损/止损平仓（防 restart / 误 clear 导致冷却蒸发）
        _db_blocked, _db_reason = _durable_reopen_blocked(
            account_id, symbol, new_position_side, new_tier=_nt or "short",
        )
        return _db_blocked, _db_reason

    # 兼容旧格式 (side, ts, tier) 和新格式 (side, ts, tier, multiplier[, effective_cd])
    if len(data) == 2:
        closed_side, ts = data
        tier = "mid"
        multiplier = 1.0
        stored_cd = None
    elif len(data) == 3:
        closed_side, ts, tier = data
        multiplier = 1.0
        stored_cd = None
    elif len(data) == 4:
        closed_side, ts, tier, multiplier = data[:4]
        stored_cd = None
    elif len(data) >= 5:
        # S0-8 新格式: (side, ts, tier, multiplier, effective_cd)
        # effective_cd 已含 sl/tp/master 延长 + 连亏倍率,优先用它
        closed_side, ts, tier, multiplier, stored_cd = data[:5]
    else:
        return False, ""

    # 若 new_tier 与 closed tier 不同且两者都明确，则放行（tier 独立）
    if _nt and tier and _nt != tier:
        return False, ""

    elapsed = time.time() - ts
    is_same_direction = (closed_side == new_position_side)

    if is_same_direction:
        # S0-8 修复:优先用 stored_cd(含 sl/tp/master 延长),否则回退到计算值
        if stored_cd is not None and stored_cd > 0:
            cooldown_sec = int(stored_cd)
        else:
            base_cd = _get_cooldown_sec(tier)
            cooldown_sec = int(base_cd * multiplier)
    else:
        cooldown_sec = _FLIP_COOLDOWN_SEC

    if elapsed >= cooldown_sec:
        if is_same_direction and matched_key:
            with _lock:
                if _state.get(matched_key) == data:
                    del _state[matched_key]
        return False, ""

    remain_sec = int(cooldown_sec - elapsed)
    remain_min = max(1, remain_sec // 60)
    side_cn = "多" if closed_side == "long" else "空"

    if is_same_direction:
        extra = ""
        if multiplier > 1.0:
            extra = f"(连续亏损x{multiplier:.0f}倍冷却)"
        reason = (
            f"刚平{side_cn}仓(tier={tier})未满{cooldown_sec//60}分钟{extra}，"
            f"抑制同向再开（约剩{remain_min}分钟）"
            f"——避免信号未变时重复付手续费"
        )
    else:
        new_cn = "多" if new_position_side == "long" else "空"
        reason = (
            f"刚平{side_cn}仓后{int(elapsed//60)}分钟即尝试开{new_cn}（反向翻转），"
            f"冷却{_FLIP_COOLDOWN_SEC//60}分钟（约剩{remain_min}分钟）"
            f"——避免频繁多空翻转双重损失"
        )

    return True, reason


def _durable_reopen_blocked(
    account_id: int,
    symbol: str,
    new_position_side: str,
    new_tier: str = "short",
) -> Tuple[bool, str]:
    """DB 耐久冷却：内存空时查最近同币种同方向亏损/止损平仓。

    防止进程重启或误 clear_state 后立刻同向再开。
    """
    try:
        from datetime import datetime, timedelta, timezone
        from backend.database.connection import SessionLocal
        from backend.database.models import PaperPosition
        from backend.services.sub_position_manager import NATURE_TO_TIER

        _tier = (new_tier or "short").strip().lower() or "short"
        if _tier == "short":
            cooldown_sec = int(os.getenv("REENTRY_SL_COOLDOWN_SEC_SHORT", "14400"))
        elif _tier == "long":
            cooldown_sec = int(os.getenv("REENTRY_SL_COOLDOWN_SEC_LONG", "172800"))
        else:
            cooldown_sec = int(os.getenv("REENTRY_SL_COOLDOWN_SEC_MID", "43200"))
        # 任意亏损也至少吃 loss cooldown
        loss_floor = int(os.getenv(
            f"REENTRY_LOSS_COOLDOWN_SEC_{_tier.upper()}",
            {"short": "14400", "mid": "14400", "long": "43200"}.get(_tier, "14400"),
        ))
        lookback = max(cooldown_sec, loss_floor)
        since = datetime.now(timezone.utc) - timedelta(seconds=lookback)
        sym_u = (symbol or "").strip().upper()

        db = SessionLocal()
        try:
            rows = (
                db.query(PaperPosition)
                .filter(
                    PaperPosition.account_id == int(account_id),
                    PaperPosition.symbol == sym_u,
                    PaperPosition.status == "closed",
                    PaperPosition.closed_at.isnot(None),
                    PaperPosition.closed_at >= since,
                )
                .order_by(PaperPosition.closed_at.desc())
                .limit(8)
                .all()
            )
        finally:
            db.close()

        now = time.time()
        for pos in rows:
            nature = (getattr(pos, "trade_nature", None) or "").strip().lower()
            pos_tier = (
                getattr(pos, "timeframe_tier", None)
                or NATURE_TO_TIER.get(nature, "")
                or ""
            )
            pos_tier = str(pos_tier).strip().lower()
            if pos_tier and pos_tier != _tier:
                continue
            side = (getattr(pos, "side", None) or "").strip().lower()
            if side != new_position_side:
                continue
            pnl = float(getattr(pos, "unrealized_pnl", 0) or 0) + float(
                getattr(pos, "partial_realized_pnl", 0) or 0
            )
            reason_l = (getattr(pos, "close_reason", None) or "").strip().lower()
            is_hard_sl = reason_l in ("sl", "stop_loss", "stop loss", "liquidation", "margin_call")
            if pnl >= 0 and not is_hard_sl:
                continue
            closed_at = getattr(pos, "closed_at", None)
            if closed_at is None:
                continue
            # paper_positions.closed_at 多为「业务本地时区 naive」（Asia/Shanghai）。
            # 若误标成 UTC，elapsed 会变成负数，同向开仓被冤杀数小时
            # （2026-08-02 ONDO：显示 -436 分钟前、仍剩 495 分钟）。
            if closed_at.tzinfo is None:
                try:
                    from zoneinfo import ZoneInfo
                    closed_at = closed_at.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
                except Exception:
                    _local_tz = datetime.now().astimezone().tzinfo or timezone.utc
                    closed_at = closed_at.replace(tzinfo=_local_tz)
            elapsed = now - closed_at.timestamp()
            if elapsed < 0:
                # 时钟/时区异常：fail-open，避免永久挡开仓
                logger.warning(
                    "[ReentryCD] %s closed_at 超前 now (elapsed=%.0fs)，跳过耐久冷却",
                    sym_u, elapsed,
                )
                continue
            need = cooldown_sec if is_hard_sl else loss_floor
            if elapsed < need:
                remain_min = max(1, int((need - elapsed) // 60))
                return True, (
                    f"DB耐久冷却: {sym_u} 刚{reason_l or '亏损'}平{side}仓"
                    f"（{int(elapsed//60)}分钟前 pnl={pnl:+.2f}），"
                    f"抑制同向再开约剩{remain_min}分钟"
                )
        return False, ""
    except Exception as e:
        logger.debug("[ReentryCD] durable check skip: %s", e)
        return False, ""


def clear_state(account_id: int, symbol: str, tier: str = "") -> None:
    """测试或手动重置用。tier 为空则清理该 symbol 所有 tier 的冷却。"""
    loss_key = f"{account_id}_{symbol}"
    with _lock:
        if tier:
            _state.pop(_state_key(account_id, symbol, tier), None)
        else:
            for _t in ("short", "mid", "long", "default"):
                _state.pop(_state_key(account_id, symbol, _t), None)
            # 也清理 v4 残留 key
            _state.pop(loss_key, None)
        _loss_history.pop(loss_key, None)


def clear_all_cooldowns() -> int:
    """一次性清空所有账户的冷却状态，主要用于调试或修复后手动放行。返回清理条数。"""
    with _lock:
        n = len(_state)
        _state.clear()
    return n


def purge_expired() -> int:
    """清理所有过期条目，防止长时间运行后内存泄漏。返回清理数量。"""
    now = time.time()
    purged = 0
    with _lock:
        # 清理超过 4 小时的 _state 条目
        stale = [k for k, v in _state.items() if now - v[1] > 4 * 3600]
        for k in stale:
            del _state[k]
        purged += len(stale)

        # 清理 _loss_history 中全部过期的 key
        empty_keys = []
        for k, hist in _loss_history.items():
            cleaned = [(pnl, ts) for pnl, ts in hist
                       if now - ts < _LOSS_HISTORY_WINDOW_SEC]
            if cleaned:
                _loss_history[k] = cleaned
            else:
                empty_keys.append(k)
        for k in empty_keys:
            del _loss_history[k]
        purged += len(empty_keys)

    # 清理减仓冷却过期条目
    with _reduce_lock:
        _rc_stale = [k for k, v in _reduce_cooldowns.items()
                     if now - v["time"] > 4 * 3600]
        for k in _rc_stale:
            del _reduce_cooldowns[k]
        purged += len(_rc_stale)
    return purged


# ══════════════════════════════════════════════════
#  减仓冷却（Partial Close Cooldown）
# ══════════════════════════════════════════════════

_reduce_lock = threading.Lock()
# key: "{account_id}_{symbol}_{side}" -> {"time": float, "tier": str, "pnl": float, "multiplier": float}
_reduce_cooldowns: Dict[str, dict] = {}


def record_partial_close(
    account_id: int, symbol: str, side: str, tier: str = "mid", close_pnl: float = 0.0,
) -> None:
    """
    记录一次减仓操作的冷却。

    Args:
        account_id: 账户ID
        symbol: 交易对
        side: 仓位方向 (long/short)
        tier: 持仓周期 (short/mid/long)
        close_pnl: 本次减仓的盈亏
    """
    if not symbol or side not in ("long", "short"):
        return

    key = f"{account_id}_{symbol}_{side}"
    multiplier = _get_loss_multiplier(account_id, symbol)

    # 记录盈亏用于连续亏损检测
    if close_pnl != 0:
        record_close_pnl(account_id, symbol, close_pnl)

    with _reduce_lock:
        _reduce_cooldowns[key] = {
            "time": time.time(),
            "tier": tier,
            "pnl": close_pnl,
            "multiplier": multiplier,
        }

    cooldown_min = _get_reduce_cooldown_minutes(tier)
    effective_min = int(cooldown_min * multiplier)
    logger.info(
        f"[ReduceCooldown] 记录减仓 account={account_id} {symbol} {side} "
        f"tier={tier} pnl={close_pnl:+.2f} "
        f"冷却{effective_min}分钟(base={cooldown_min}min x{multiplier:.0f}倍)"
    )


def _get_reduce_cooldown_minutes(tier: str) -> int:
    """根据 tier 获取减仓冷却时间（分钟）"""
    return {"short": 15, "mid": 30, "long": 60}.get(tier, 30)


# ══════════════════════════════════════════════════
#  M2 — ai_reverse 冷却（P3，2026-04-22）
#
#  证据锚：7 天 12 次 ai_reverse 里 ASTER 3 / BNB 4 / XPL 2 都是同
#  symbol 反复反向，胜率 17% 总亏 -17U。新增 60min 同 symbol 冷却：
#  一旦刚翻转过，60 分钟内不允许再翻转同一品种。
#
#  与原 _FLIP_COOLDOWN_SEC（30min）的区别：
#    - _FLIP_COOLDOWN_SEC 针对"全平后反向开仓"
#    - 本冷却专门针对 AI 主动翻转（close_and_open），条件更严
# ══════════════════════════════════════════════════

_ai_reverse_lock = threading.Lock()
# key: "{account_id}_{symbol}" -> unix_ts
_ai_reverse_last: Dict[str, float] = {}


def record_ai_reverse(account_id: int, symbol: str) -> None:
    """记录一次 AI 主动反向开仓。"""
    if not symbol:
        return
    key = f"{account_id}_{symbol}"
    with _ai_reverse_lock:
        _ai_reverse_last[key] = time.time()
    logger.info(f"[AiReverseCooldown] 记录 account={account_id} {symbol}")


def is_ai_reverse_blocked(
    account_id: int, symbol: str, cooldown_sec: int,
) -> Tuple[bool, str]:
    """
    检查是否应当拦截新的 ai_reverse（同 symbol 冷却）。

    Args:
        cooldown_sec: 冷却秒数；≤ 0 视为禁用冷却（直接放行）。
    """
    if cooldown_sec <= 0 or not symbol:
        return False, ""
    key = f"{account_id}_{symbol}"
    with _ai_reverse_lock:
        last_ts = _ai_reverse_last.get(key)
    if last_ts is None:
        return False, ""
    elapsed = time.time() - last_ts
    if elapsed >= cooldown_sec:
        # 过期清理
        with _ai_reverse_lock:
            if _ai_reverse_last.get(key) == last_ts:
                del _ai_reverse_last[key]
        return False, ""
    remain_min = max(1, int((cooldown_sec - elapsed) / 60))
    reason = (
        f"刚反向开仓过({int(elapsed/60)}分钟前)，"
        f"同 symbol ai_reverse 冷却 {cooldown_sec//60} 分钟，约剩 {remain_min} 分钟"
    )
    return True, reason


def clear_ai_reverse(account_id: int, symbol: str) -> None:
    """测试或手动重置用。"""
    with _ai_reverse_lock:
        _ai_reverse_last.pop(f"{account_id}_{symbol}", None)


def is_reduce_cooling_down(
    account_id: int, symbol: str, side: str, tier: str = "mid",
) -> Tuple[bool, str]:
    """
    检查减仓冷却状态。

    Returns:
        (is_cooling: bool, reason: str)
    """
    key = f"{account_id}_{symbol}_{side}"
    with _reduce_lock:
        entry = _reduce_cooldowns.get(key)
    if not entry:
        return False, ""

    cooldown_min = _get_reduce_cooldown_minutes(tier)
    multiplier = entry.get("multiplier", 1.0)
    effective_min = cooldown_min * multiplier

    elapsed_min = (time.time() - entry["time"]) / 60
    if elapsed_min >= effective_min:
        # 冷却已过期，清理
        with _reduce_lock:
            if _reduce_cooldowns.get(key) == entry:
                del _reduce_cooldowns[key]
        return False, ""

    remain_min = max(1, int(effective_min - elapsed_min))
    extra = ""
    if multiplier > 1.0:
        extra = f"(连续亏损x{multiplier:.0f}倍冷却)"
    reason = f"减仓冷却中{extra}，剩余约{remain_min}分钟(tier={tier})"
    return True, reason
