"""midlong_circuit_gate — 中长线（mid/long tier）开仓熔断器。

[2026-08-29 全面修复 · P1.4] 数据依据（trade_facts 8/2-8/29）：
  - mid 层 233 笔 -325.31，其中空头 -601.73（avg -4.97/笔）；
  - 8/12-13 VELVET 两天 54 笔空 -410：同价位反复开空打损（单笔 -90 级），
    mid 单笔风险是 scalp 的 ~35 倍（SL avg -41.38 vs -1.18）却无任何熔断；
  - scalp 层早有 short_tier_entry_gate 熔断，mid/long 层完全裸奔。

本模块补齐：
  1. 连续亏损熔断：同 (account, symbol) 连亏 N 笔（默认 3，mid 单笔损失大，
     阈值比 scalp 的 8 更紧）→ 冷却 12h；
  2. 单 symbol 日亏上限：当日累计净亏（含费）≤ -cap（默认 60 USD，env 可配）
     → 冷却到次日；
  3. 状态落盘 data/midlong_circuit_state.json（重启不丢，沿 short_tier 惯例）。

P1.5：MIDLONG_OPEN_SHORT_ENABLED=false（默认）暂停 mid/long 新开空头
（mid 空头 30 天 -601 是最差象限；多头 +276 唯一健康）。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 配置（env 直读，免 settings 循环依赖；settings 可后续透传）──
def _env_f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _env_b(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or ("true" if default else "false")).strip().lower()
    return raw in ("1", "true", "yes", "on")


CONSEC_LOSSES_LIMIT = int(_env_f("MIDLONG_CIRCUIT_CONSEC_LOSSES", 3) or 3)
COOLDOWN_S = int(_env_f("MIDLONG_CIRCUIT_COOLDOWN_S", 12 * 3600) or 12 * 3600)
DAILY_LOSS_CAP = _env_f("MIDLONG_CIRCUIT_DAILY_LOSS_CAP", 60.0)  # USD，≤0 关闭
SHORT_OPEN_ENABLED = _env_b("MIDLONG_OPEN_SHORT_ENABLED", False)


def _short_mode() -> str:
    """[2026-08-29 v2] mid 空头模式：off=全停 | conditional=有下行证据才放行
    （默认；4h 偏空 或 24h 跌≥2%，配合熔断闸托底）| on=无条件放行。
    全停被实测否决：VELVET 式灾难靠熔断闸(连亏3→12h+日亏帽)防，一刀切
    只会让系统半边瘫。"""
    if SHORT_OPEN_ENABLED:
        return "on"
    return (os.getenv("MIDLONG_SHORT_MODE", "conditional") or "conditional").strip().lower()


def _short_bias_ok(market_summary: Optional[dict], symbol: str) -> bool:
    """conditional 模式的下行证据：orchestrator mid_bias=bearish 或 24h 跌≥2%。"""
    try:
        ms = (market_summary or {}).get(symbol) or {}
        if not isinstance(ms, dict):
            return False
        orch = ms.get("orchestrator") if isinstance(ms.get("orchestrator"), dict) else {}
        if str(orch.get("mid_bias") or "").strip().lower() == "bearish":
            return True
        chg24 = float(ms.get("price_change_24h_pct") or 0)
        if chg24 <= -0.02:
            return True
    except Exception:
        return False
    return False

_STATE_FILE = os.path.join("data", "midlong_circuit_state.json")
_state: Dict[str, dict] = {}
_loaded = False


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                _state.update(d)
                logger.info("[MidCircuit] 已加载熔断状态 %d 条", len(d))
    except Exception as exc:
        logger.warning("[MidCircuit] 状态加载失败(按空状态启动): %s", exc)


def _save() -> None:
    try:
        os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
        _tmp = _STATE_FILE + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as f:
            json.dump(_state, f, ensure_ascii=False, indent=2)
        os.replace(_tmp, _STATE_FILE)
    except Exception as exc:
        logger.warning("[MidCircuit] 状态落盘失败: %s", exc)


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _acct_key(account_id: Optional[int], symbol: str) -> str:
    return f"{int(account_id or 0)}:{(symbol or '').upper()}"


def check_midlong_entry(
    account_id: Optional[int],
    symbol: str,
    side: str = "",
    tier: str = "mid",
    market_summary: Optional[dict] = None,
) -> Tuple[bool, str]:
    """中长线新开仓检查。返回 (allowed, reason)；fail-open（异常放行）。

    [2026-08-29 v2] 空头策略：conditional（默认）= 有下行证据（4h 偏空或
    24h 跌≥2%）才放行，熔断闸托底；off=全停（回滚）；on=无条件。
    master lane（无 market_summary 上下文）在 conditional 下视为无证据 → 拦。"""
    try:
        if not _env_b("MIDLONG_CIRCUIT_ENABLED", True):
            return True, ""
        _load()
        side_l = (side or "").lower()
        if side_l in ("sell", "short"):
            _mode = _short_mode()
            if _mode == "off":
                return False, "midlong_short_off: mid空头全停(MIDLONG_SHORT_MODE=off)"
            if _mode == "conditional" and not _short_bias_ok(market_summary, (symbol or "").upper()):
                return False, (
                    "midlong_short_no_bias: mid空头需下行证据(4h偏空或24h跌≥2%),"
                    "熔断闸(连亏3→12h+日亏帽)兜底"
                )
        key = _acct_key(account_id, symbol)
        st = _state.get(key)
        if not st:
            return True, ""
        now = time.time()
        banned_until = float(st.get("banned_until", 0) or 0)
        if banned_until and now < banned_until:
            remain_min = int((banned_until - now) / 60)
            return False, (
                f"mid_circuit_banned: {symbol} 连亏{int(st.get('consec_losses', 0))}笔"
                f"/日亏{st.get('day_pnl', 0):.1f} 冷却剩余{remain_min}min"
            )
        if banned_until and now >= banned_until:
            st["banned_until"] = 0
            st["consec_losses"] = 0
        return True, ""
    except Exception as exc:
        logger.debug("[MidCircuit] 检查异常(fail-open): %s", exc)
        return True, ""


def record_midlong_outcome(
    account_id: Optional[int],
    symbol: str,
    net_pnl: float,
) -> None:
    """平仓后记录净盈亏（调用方传 net=pnl-fee），更新熔断状态。"""
    try:
        if not _env_b("MIDLONG_CIRCUIT_ENABLED", True):
            return
        _load()
        key = _acct_key(account_id, symbol)
        today = _today()
        st = _state.setdefault(
            key, {"consec_losses": 0, "day": today, "day_pnl": 0.0, "banned_until": 0}
        )
        # 跨天重置日累计
        if st.get("day") != today:
            st["day"] = today
            st["day_pnl"] = 0.0
        st["day_pnl"] = round(float(st.get("day_pnl", 0.0)) + float(net_pnl or 0), 4)
        now = time.time()
        # [2026-08-31 根治] 冷却到期即在记录侧清零，不再依赖"下次开仓检查"才重置。
        _banned_until = float(st.get("banned_until", 0) or 0)
        if _banned_until and now >= _banned_until:
            st["banned_until"] = 0
            st["consec_losses"] = 0
        # 本次平仓时是否仍在冷却中（到期清零后为 False）
        _in_cooldown = float(st.get("banned_until", 0) or 0) > now
        if float(net_pnl or 0) < 0:
            st["consec_losses"] = int(st.get("consec_losses", 0)) + 1
            # [2026-08-31 根治] 冷却期内继续亏损不再顺延 12h——否则亏损不止、
            # 熔断永不解除（实测 BNB 连亏 98 笔被反复续期）。连亏计数仍保留，
            # 但 ban 时长封顶为"触发时 + COOLDOWN_S"；日亏帽仍可延长到次日。
            if st["consec_losses"] >= CONSEC_LOSSES_LIMIT and not _in_cooldown:
                st["banned_until"] = now + COOLDOWN_S
                logger.warning(
                    "[MidCircuit] 🚫 %s 连续亏损 %d 笔 → 冷却 %dh",
                    key, st["consec_losses"], COOLDOWN_S // 3600,
                )
        else:
            st["consec_losses"] = 0
        # 日亏上限（跨过即熔断到次日 00:00 本地）
        if DAILY_LOSS_CAP > 0 and float(st["day_pnl"]) <= -abs(DAILY_LOSS_CAP):
            try:
                _next_day = time.mktime(time.strptime(today, "%Y-%m-%d")) + 86400
                st["banned_until"] = max(float(st.get("banned_until", 0) or 0), _next_day)
                logger.warning(
                    "[MidCircuit] 🚫 %s 当日累计净亏 %.2f ≤ -%.0f → 熔断至次日",
                    key, float(st["day_pnl"]), abs(DAILY_LOSS_CAP),
                )
            except Exception:
                st["banned_until"] = now + COOLDOWN_S
        _save()
    except Exception as exc:
        logger.debug("[MidCircuit] 结果记录异常: %s", exc)
