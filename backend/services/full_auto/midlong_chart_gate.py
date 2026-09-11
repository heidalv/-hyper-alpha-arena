# -*- coding: utf-8 -*-
"""中长线开仓的多模态图审信号闸（2026-09-05，图审管线接入中长线决策）。

背景（2026-08-31 周报）：swing 14 天净亏 -11.5、胜率 31.2%，**亏损全平后 24h
同向再开率 70%**（目标 ≤20%）——规则引擎在亏损后仍反复放行同向开仓。
本闸用间隔扫描的多模态趋势图审信号（dual:trend_chart_review，双票共识
≥0.7 才入 signal_ledger）做三件事：

1) 持仓建议禁令：position_advice=no_new_long/no_new_short → 拒开对应方向。
2) 强反向拦截：最新图审方向与开仓方向相反且 strength ≥ 5 → 拒开。
3) 亏损后再开需同意：24h 内该币该方向有亏损全平记录时，同向再开必须
   拿到图审同向支持（direction 一致且 strength ≥ 4），否则拒开。

失败语义：默认 fail-open。图审是附加证据，没有图照样分析、照样可以开。
``MIDLONG_CHART_REQUIRED=true`` 才会把缺图当成禁开（紧急收紧，不是默认）。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

SOURCE = "dual:trend_chart_review"


def _enabled() -> bool:
    return os.getenv("MIDLONG_CHART_GATE_ENABLED", "true").lower() in ("1", "true", "yes", "on")


def _lookback_h() -> float:
    try:
        return max(1.0, float(os.getenv("MIDLONG_CHART_GATE_LOOKBACK_H", "16")))
    except ValueError:
        return 16.0


def _conflict_strength() -> float:
    try:
        return float(os.getenv("MIDLONG_CHART_GATE_CONFLICT_STRENGTH", "5"))
    except ValueError:
        return 5.0


def _support_strength() -> float:
    try:
        return float(os.getenv("MIDLONG_CHART_GATE_SUPPORT_STRENGTH", "4"))
    except ValueError:
        return 4.0


def _advice_ttl_min() -> int:
    """`position_advice` 这类**立场型**建议的独立有效期（分钟，0 = 不启用该特例）。

    [P11 执行 2026-09-10] 实测（`_audit_ml/Z178`，48h）：图审否决 **1,419 条**，其中
    **1,392 条（98.1%）**来自单条 `position_advice=no_new_long`，而**审计行里没有年龄字段**
    ⇒ 无法判定"这条禁令到底新不新鲜"。立场型建议与行情信号不同：它是模型对**方向立场**的表态，
    图审 8h 一轮，一条 `no_new_long` 会以通用上限（默认 240min）持续压住多头通道 —— 即每轮
    最多 50% 时间无法开多。故给它一个**更短**的独立有效期，并把它写进否决原因（可追溯）。
    """
    try:
        return int(os.getenv("MIDLONG_CHART_ADVICE_TTL_MIN", "180") or 180)
    except (TypeError, ValueError):
        return 180


def _latest_chart_signal(symbol: str) -> Optional[Dict[str, Any]]:
    """最新且未过期的图审共识信号（入账即 accepted；无则 None → 放行）。"""
    try:
        from backend.services.analysis import ledgers

        rows = ledgers.list_signals(source=SOURCE, symbol=symbol, limit=5)
        cutoff = time.time() * 1000 - _lookback_h() * 3600 * 1000
        for r in rows or []:
            try:
                if int(r.get("created_ms") or 0) >= cutoff:
                    return r
            except (TypeError, ValueError):
                continue
        return None
    except Exception as exc:
        # [§59 修复] fail-open 必须可见（§41.2 只覆盖了 4 个入口模块，本模块漏项）
        logger.warning("[midlong_chart_gate] 信号查询失败(fail-open，本次不否决): %s", exc)
        return None


def _recent_loss_close(symbol: str, side: str, hours: float = 24.0) -> Optional[Dict[str, Any]]:
    """近 N 小时该币该方向最后一次亏损全平（无/非亏/异常 → None）。

    净盈亏近似 = 价差×方向×size + 累计部分平仓盈亏（分档 TP 的部分盈利
    会抵扣，避免「分批止盈后止损」被误判成纯亏损）。
    """
    db = None
    try:
        from sqlalchemy import text

        from backend.database.connection import SessionLocal

        db = SessionLocal()
        row = db.execute(
            text("""
                SELECT id, side, size, entry_price, close_price, partial_realized_pnl, closed_at
                FROM paper_positions
                WHERE symbol = :sym AND side = :side AND status = 'closed'
                  AND closed_at >= now() - make_interval(hours => :h)
                ORDER BY closed_at DESC LIMIT 1
            """),
            {"sym": symbol, "side": side, "h": int(hours)},
        ).mappings().first()
        if not row:
            return None
        entry = float(row["entry_price"] or 0)
        close = float(row["close_price"] or 0)
        size = float(row["size"] or 0)
        if entry <= 0 or close <= 0 or size <= 0:
            return None
        direction = 1 if str(row["side"]).lower() == "long" else -1
        net = (close - entry) * direction * size + float(row["partial_realized_pnl"] or 0)
        if net >= 0:
            return None
        return {"position_id": int(row["id"]), "net_pnl": round(net, 4),
                "closed_at": str(row["closed_at"])[:19]}
    except Exception as exc:
        # [§59 修复] 这条=「亏损后再开需图审同意」这道闸被跳过，必须可见
        logger.warning("[midlong_chart_gate] 亏损平仓查询失败(fail-open，跳过亏损后再开检查): %s", exc)
        return None
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def _chart_required() -> bool:
    try:
        from backend.config.settings import MIDLONG_CHART_REQUIRED
        return bool(MIDLONG_CHART_REQUIRED)
    except Exception:
        return os.getenv("MIDLONG_CHART_REQUIRED", "false").lower() in ("1", "true", "yes", "on")


def chart_gate_check(
    symbol: str,
    action: str,
    *,
    tier: str = "",
    trade_nature: str = "",
) -> Tuple[bool, str, Dict[str, Any]]:
    """开仓前的图审否决检查。返回 (allow, reason, detail)。"""
    if not _enabled():
        return True, "chart_gate 未启用", {}
    sym = str(symbol or "").upper()
    act = str(action or "").lower()
    if act not in ("buy", "sell"):
        return True, "非开仓动作", {}
    dir_int = 1 if act == "buy" else -1
    side = "long" if dir_int > 0 else "short"

    sig = _latest_chart_signal(sym)
    if not sig:
        if _chart_required():
            return False, "chart_gate: 无新鲜图审信号（required）", {"signal": None}
        return True, "chart_gate: 无新鲜图审信号（fail-open）", {"signal": None}

    try:
        sig_dir = int(sig.get("direction") or 0)
        sig_strength = float(sig.get("strength") or 0)
    except (TypeError, ValueError):
        return True, "chart_gate: 信号字段异常（fail-open）", {"signal": "malformed"}
    payload = sig.get("payload") if isinstance(sig.get("payload"), dict) else {}
    advice = str(payload.get("position_advice") or "").lower()

    detail: Dict[str, Any] = {
        "signal_age_min": int(max(0, (time.time() * 1000 - int(sig.get("created_ms") or 0)) / 60000)),
        "signal_direction": sig_dir,
        "signal_strength": sig_strength,
        "position_advice": advice,
        "open_direction": dir_int,
        "tier": str(tier or ""),
        "nature": str(trade_nature or ""),
    }

    # [2026-09-08] 陈旧图审信号不许否决：图审 8h 一轮，信号可能已隔 12h+。
    # 用隔夜的"强多/强空"否决当前主脑的新鲜决策是错杀（UNI 实证：12h 前强多图
    # 否决了当前超买回落空）。超过 max_age（默认 240min=4h）的信号 fail-open 不否决。
    _max_age = int(os.getenv("MIDLONG_CHART_MAX_SIGNAL_AGE_MIN", "240") or 240)
    if detail["signal_age_min"] > _max_age:
        return True, f"chart_gate: 图审信号陈旧({detail['signal_age_min']}min>{_max_age}min)，不否决（fail-open）", detail

    # 1) 持仓建议禁令（P11：立场型建议另有更短的独立有效期）
    _adv_ttl = _advice_ttl_min()
    _age = detail["signal_age_min"]
    if advice in ("no_new_long", "no_new_short") and _adv_ttl > 0 and _age > _adv_ttl:
        return (
            True,
            f"chart_gate: 图审立场建议陈旧({_age}min>{_adv_ttl}min) advice={advice}，不否决（fail-open）",
            detail,
        )
    if dir_int > 0 and advice == "no_new_long":
        return False, f"chart_gate_veto: 图审 position_advice=no_new_long (age={_age}min ttl={_adv_ttl}min)", detail
    if dir_int < 0 and advice == "no_new_short":
        return False, f"chart_gate_veto: 图审 position_advice=no_new_short (age={_age}min ttl={_adv_ttl}min)", detail

    # 2) 强反向拦截
    if sig_dir != 0 and sig_dir == -dir_int and sig_strength >= _conflict_strength():
        return False, f"chart_gate_veto: 图审强反向(dir={sig_dir}, strength={sig_strength}, age={_age}min)", detail

    # 3) 亏损全平后同向再开：必须有图审同向支持
    loss = _recent_loss_close(sym, side)
    if loss:
        detail["recent_loss_close"] = loss
        if sig_dir == dir_int and sig_strength >= _support_strength():
            return True, "chart_gate: 亏损后同向再开，图审同向支持放行", detail
        return False, (f"chart_gate_veto: 24h内{side}亏损全平且图审未给同向支持"
                       f"(dir={sig_dir}, s={sig_strength}, age={_age}min)"), detail

    return True, "chart_gate: 图审信号无冲突", detail
