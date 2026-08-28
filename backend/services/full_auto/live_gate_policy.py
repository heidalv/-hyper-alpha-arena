# -*- coding: utf-8 -*-
"""实盘短线引导期（bootstrap）门槛策略。

背景（2026-08-28 实盘零成交根因排查）：
实盘账户（币安 188）自身没有任何成交样本，但所有入场门槛都由**模拟盘数据**
标定或收紧：
  - scalp_factor_router 自适应门槛：读 paper_positions 3 天胜率 → 亏损币门槛
    抬高到 50（live）——实盘被模拟盘的连亏历史锁死；
  - decision_fusion_arbiter pwin 地板：回放标定 + RR 安全系数，与实盘无样本无关；
  - V5 unified_gate 置信度门：runtime governor 基于 paper 反馈把 scalp 门收到
    55~70，实盘信号分布（27~48）永远够不到；
  - scalp_ev_gate：live 要求 EV ≥ +0.03% 且 meta 硬过滤 pwin ≥ 0.5，在当前
    校准 p_win≈0.34、TP 实现率 0.55 的口径下数学上不可达（实盘零成交）；
  - short_tier_entry_gate 币种熔断：全局 key（无账户维度），paper 的 BTC 2913
    笔连亏把实盘 BTC 也禁了。

引导期定义：LIVE_SCALP_BOOTSTRAP_ENABLED=true 且该实盘账户累计平仓 scalp
样本 < LIVE_SCALP_BOOTSTRAP_MIN_TRADES（默认 20）时，对**实盘短线**应用以下
受控放宽（每项均可 env 覆盖/关闭，期满自动回落到严格默认）：
  - pwin 地板：FUSION_SCALP_PWIN_MIN_LIVE（默认 0.45，绝对下限，不再叠加 RR
    安全系数）——低于保本线（RR1.3 时 0.435）不远，有边界；
  - V5 短线置信度门：LIVE_SCALP_V5_MIN_CONFIDENCE（默认 45）；
  - EV 地板：LIVE_SCALP_EV_MIN_PCT_BOOTSTRAP（默认 -0.01，与 paper 样本期
    同档）且 meta 硬过滤降级为软接入（同 paper）；
  - 每日开仓上限：LIVE_SCALP_DAILY_OPEN_CAP（默认 5 笔，配合小资金仓位，
    单笔风险仍被 SCALP_MAX_TRADE_RISK_PCT 硬顶）。

安全边界（引导期不放宽的部分）：FlashVeto LLM 否决、微结构守卫、手续费守卫、
RR 下限、冷却、层预算、同向冷却、宪法风控（余额/权益/日亏熔断）、止损硬顶。
所有改动只影响 live；paper 行为完全不变。

注意：实盘仓位/订单目前落库在 paper_positions/paper_orders（account_id=188），
样本计数同时统计 paper_positions 与 live_sub_positions 的已平仓 scalp 记录。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

_BOOTSTRAP_ENABLED = os.getenv("LIVE_SCALP_BOOTSTRAP_ENABLED", "true").strip().lower() in (
    "true", "1", "yes", "on",
)
_MIN_TRADES = int(os.getenv("LIVE_SCALP_BOOTSTRAP_MIN_TRADES", "20") or 20)

# TTL 缓存：实盘账户平仓样本数是慢变量，60s 内复用，避免热路径反复开连接。
_cache: dict = {"lock": threading.Lock(), "ts": 0.0, "value": None}


def live_bootstrap_enabled() -> bool:
    """引导期总开关（读 env，允许运行中改）。"""
    return os.getenv("LIVE_SCALP_BOOTSTRAP_ENABLED", "true").strip().lower() in (
        "true", "1", "yes", "on",
    )


def live_scalp_closed_samples(account_id: Optional[int]) -> int:
    """该实盘账户已平仓短线样本数（paper_positions + live_sub_positions）。"""
    if not account_id:
        return 0
    try:
        from backend.database.connection import SessionLocal
        from sqlalchemy import text

        from backend.core.tenant import set_system_identity
        set_system_identity()
        db = SessionLocal()
        try:
            n = 0
            row = db.execute(
                text(
                    "SELECT count(*) FROM paper_positions "
                    "WHERE account_id = :aid AND status = 'closed' "
                    "AND trade_nature = 'scalp'"
                ),
                {"aid": int(account_id)},
            ).fetchone()
            n += int(row[0] or 0)
            try:
                row2 = db.execute(
                    text(
                        "SELECT count(*) FROM live_sub_positions "
                        "WHERE account_id = :aid AND status = 'closed'"
                    ),
                    {"aid": int(account_id)},
                ).fetchone()
                n += int(row2[0] or 0)
            except Exception:
                pass  # 表/列不存在时忽略，不影响主口径
            return n
        finally:
            db.close()
    except Exception as exc:
        logger.debug("[LiveGatePolicy] 实盘平仓样本统计失败: %s", exc)
        return 0


def live_bootstrap_active(account_id: Optional[int] = None) -> bool:
    """引导期是否生效：开关开 且 该账户平仓 scalp 样本 < 下限。"""
    if not live_bootstrap_enabled():
        return False
    if not account_id:
        return True  # 无法确认账户样本时按引导期处理（更宽松但仍有每日上限兜底）
    with _cache["lock"]:
        if _cache["ts"] and time.time() - _cache["ts"] < 60.0:
            return bool(_cache["value"])
    n = live_scalp_closed_samples(account_id)
    active = n < max(1, int(_MIN_TRADES))
    with _cache["lock"]:
        _cache["ts"] = time.time()
        _cache["value"] = active
    return active


def live_scalp_pwin_floor() -> float:
    """实盘引导期 pwin 地板（绝对下限，不叠加 RR 安全系数）。"""
    try:
        return max(0.35, min(0.55, float(os.getenv("FUSION_SCALP_PWIN_MIN_LIVE", "0.45") or 0.45)))
    except (TypeError, ValueError):
        return 0.45


def live_scalp_v5_confidence() -> int:
    """实盘引导期 V5 短线置信度门槛。"""
    try:
        return max(30, min(70, int(os.getenv("LIVE_SCALP_V5_MIN_CONFIDENCE", "45") or 45)))
    except (TypeError, ValueError):
        return 45


def live_scalp_v5_min_rr() -> float:
    """实盘引导期 V5 短线最低盈亏比（默认 1.3，与 paper 同档）。

    live 基准 V5_SCALP_MIN_RR=1.4，而 gate/结构止损产出的 TP1.5%/SL1.15%
    RR≈1.30 会被 1.4 拦死（实测 XRP Gate 通过后 RR 门拦截）。引导期回落到
    1.3；低于 1.3 的结构仍拦（保本线保护）。回滚：LIVE_SCALP_V5_MIN_RR=1.4。
    """
    try:
        return max(1.1, min(1.6, float(os.getenv("LIVE_SCALP_V5_MIN_RR", "1.3") or 1.3)))
    except (TypeError, ValueError):
        return 1.3


def live_scalp_ev_min() -> float:
    """实盘引导期 EV 地板（与 paper 样本期同档，默认 -1.0%）。"""
    try:
        return float(os.getenv("LIVE_SCALP_EV_MIN_PCT_BOOTSTRAP", "-0.0100") or -0.0100)
    except (TypeError, ValueError):
        return -0.0100


def live_scalp_ev_meta_hard_filter_off() -> bool:
    """实盘引导期是否把 EV 闸的 meta 硬过滤降级为软接入。

    [2026-08-28 用户指令：实盘收紧] 默认改为 false——实盘保持 meta 硬过滤，
    不再与 paper 同档软放行。
    """
    return os.getenv("LIVE_SCALP_EV_META_HARD_FILTER", "false").strip().lower() in (
        "true", "1", "yes", "on",
    )


def live_pwin_extra() -> float:
    """实盘 pwin 地板加严量（在"不低于 paper 有效地板"基础上叠加）。

    [2026-08-28 用户指令：实盘收紧] 实盘=真金白银，pwin 门槛高于模拟盘。
    """
    try:
        return max(0.0, min(0.15, float(os.getenv("LIVE_PWIN_EXTRA", "0.03") or 0.03)))
    except (TypeError, ValueError):
        return 0.03


def live_v5_conf_extra() -> int:
    """实盘 V5 置信度门槛加严量（百分点）。"""
    try:
        return max(0, min(30, int(os.getenv("LIVE_V5_CONF_EXTRA", "5") or 5)))
    except (TypeError, ValueError):
        return 5


def master_conf_extra() -> int:
    """实盘 master 入场置信门槛加严量（百分点，与 V5 同源可独立调）。"""
    try:
        return max(0, min(30, int(os.getenv("LIVE_CONF_EXTRA", "8") or 8)))
    except (TypeError, ValueError):
        return 8


def live_score_extra() -> int:
    """实盘因子评分/执行门槛加严量（分）。"""
    try:
        return max(0, min(40, int(os.getenv("LIVE_SCORE_EXTRA", "10") or 10)))
    except (TypeError, ValueError):
        return 10


def live_budget_mult() -> float:
    """实盘资金分配乘数：层预算 = 模拟盘口径 × 该系数（收紧仓位）。"""
    try:
        return max(0.2, min(1.0, float(os.getenv("LIVE_BUDGET_MULT", "0.6") or 0.6)))
    except (TypeError, ValueError):
        return 0.6


def live_daily_open_cap() -> int:
    """实盘每日开单总上限（全部 tier 合计；0=不限制）。"""
    try:
        return max(0, int(os.getenv("LIVE_DAILY_OPEN_CAP", "6") or 6))
    except (TypeError, ValueError):
        return 6


_QUOTA_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "live_open_quota",
)
_quota_lock = threading.Lock()


def _quota_path(session_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(session_id))
    return os.path.join(_QUOTA_DIR, f"{safe}.json")


def live_daily_open_used(session_id: str) -> int:
    """今日实盘已开单计数（按会话按日，文件落盘）。"""
    try:
        p = _quota_path(session_id)
        if os.path.exists(p):
            import json as _json
            with open(p, "r", encoding="utf-8") as f:
                data = _json.load(f) or {}
            if isinstance(data, dict) and data.get("date") == time.strftime("%Y-%m-%d"):
                return int(data.get("used", 0) or 0)
    except Exception as e:
        logger.debug("[LiveGatePolicy] quota read fail %s: %s", session_id, e)
    return 0


def live_daily_open_bump(session_id: str) -> int:
    """实盘开单计数 +1，返回最新计数。"""
    with _quota_lock:
        used = live_daily_open_used(session_id) + 1
        try:
            import json as _json
            os.makedirs(_QUOTA_DIR, exist_ok=True)
            tmp = _quota_path(session_id) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                _json.dump({"date": time.strftime("%Y-%m-%d"), "used": used}, f)
            os.replace(tmp, _quota_path(session_id))
        except Exception as e:
            logger.warning("[LiveGatePolicy] quota write fail %s: %s", session_id, e)
        return used


def live_enforce_daily_open_cap(session_id: str) -> tuple:
    """实盘开单前校验每日总配额。返回 (allowed, used, cap)。"""
    cap = live_daily_open_cap()
    if cap <= 0:
        return (True, live_daily_open_used(session_id), 0)
    used = live_daily_open_used(session_id)
    if used >= cap:
        return (False, used, cap)
    live_daily_open_bump(session_id)
    return (True, used + 1, cap)


def live_scalp_daily_open_cap() -> int:
    """实盘引导期每日开仓上限（0=不限制）。"""
    try:
        return max(0, int(os.getenv("LIVE_SCALP_DAILY_OPEN_CAP", "5") or 5))
    except (TypeError, ValueError):
        return 5
