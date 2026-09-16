"""TP/SL 初始价格计算 — 从 monolith 迁出（整改#8 Phase2 执行层瘦身）。"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def compute_initial_tp_sl_prices(
    tier: str,
    action: str,
    ref_price: float,
    atr_pct: float = 0.0,
    sym: str = "",
    atr_1d_pct: float = 0.0,
    dec: Optional[dict] = None,
) -> tuple[float, float, str]:
    """计算初始 TP/SL 价格。

    Returns:
        (tp_price, sl_price, tp_sl_source)
    """
    from backend.config.settings import (
        TIER_TP_SL_DEFAULTS,
        TIER_ATR_MULTIPLIER,
        RISK_USE_VOL_BAND_DEFAULTS,
        RISK_USE_VOL_BAND_ATR_MULT,
    )

    sl_price = 0.0
    tp_price = 0.0
    tp_sl_source = "fixed_fallback"

    if ref_price <= 0:
        return (tp_price, sl_price, tp_sl_source)

    try:
        from backend.config.settings import MIDLONG_AGENT_SL_TO_EXECUTE

        _dec = dec if isinstance(dec, dict) else {}
        _env = _dec.get("_agent_envelope") if isinstance(_dec.get("_agent_envelope"), dict) else {}
        _agent_src = (_env.get("agent_source") or _dec.get("_decision_source") or "").lower()
        _use_agent = MIDLONG_AGENT_SL_TO_EXECUTE and (
            _dec.get("_agent_independent") or _agent_src in ("swing_agent", "trend_agent")
        )
        if _use_agent:
            _sl_p = float(_env.get("structure_sl_price") or 0)
            _tp_p = float(_env.get("structure_tp_price") or 0)
            _sl_pct = float(_env.get("sl_pct") or _dec.get("stop_loss_pct") or 0)
            _tp_pct = float(_env.get("tp_pct") or _dec.get("take_profit_pct") or 0)
            if _sl_p > 0:
                sl_price = round(_sl_p, 6)
                tp_sl_source = _env.get("sl_source") or "agent_structure_sl"
            elif _sl_pct > 0:
                if action == "buy":
                    sl_price = round(ref_price * (1 - _sl_pct), 6)
                else:
                    sl_price = round(ref_price * (1 + _sl_pct), 6)
                tp_sl_source = "agent_sl_pct"
            if _tp_p > 0:
                tp_price = round(_tp_p, 6)
            elif _tp_pct > 0:
                if action == "buy":
                    tp_price = round(ref_price * (1 + _tp_pct), 6)
                else:
                    tp_price = round(ref_price * (1 - _tp_pct), 6)
            if sl_price > 0:
                return (tp_price, sl_price, tp_sl_source)
    except Exception as _agent_sl_err:
        logger.debug("[TP/SL] Agent SL 路径跳过: %s", _agent_sl_err)

    from backend.services.risk_band_resolver import (
        stage_e_active,
        get_vol_band,
        get_tp_sl_defaults,
        get_atr_multiplier,
    )
    try:
        from backend.config.settings import (
            RISK_USE_TIER_TP_SL_V2,
            RISK_USE_LONG_TIER_1D_ATR,
            TIER_TP_SL_DEFAULTS_V2,
            LONG_TIER_ATR_1D_MULTIPLIER,
        )
    except Exception:
        RISK_USE_TIER_TP_SL_V2 = False
        RISK_USE_LONG_TIER_1D_ATR = False
        TIER_TP_SL_DEFAULTS_V2 = {}
        LONG_TIER_ATR_1D_MULTIPLIER = {}

    _band: Optional[str] = None
    _use_band_defaults = stage_e_active() and RISK_USE_VOL_BAND_DEFAULTS and bool(sym)
    _use_band_atr_mult = stage_e_active() and RISK_USE_VOL_BAND_ATR_MULT and bool(sym)
    _use_v2 = stage_e_active() and RISK_USE_TIER_TP_SL_V2 and bool(sym)
    _use_1d_atr_long = stage_e_active() and RISK_USE_LONG_TIER_1D_ATR and tier == "long"

    if _use_band_defaults or _use_band_atr_mult or _use_v2:
        try:
            _band = get_vol_band(sym)
        except Exception as _e:
            logger.warning(f"[FullAuto][StageE] get_vol_band({sym}) 失败: {_e}, 退回旧路径")
            _band = None

    if _use_v2 and _band is not None:
        tp_sl_def = TIER_TP_SL_DEFAULTS_V2.get(_band, {}).get(tier, {})
        tp_sl_source = f"p2_v2_band_{_band}_{tier}"
    elif _use_band_defaults and _band is not None:
        tp_sl_def = get_tp_sl_defaults(_band, tier)
        tp_sl_source = f"stage_e_band_{_band}"
    else:
        tp_sl_def = TIER_TP_SL_DEFAULTS.get(tier, TIER_TP_SL_DEFAULTS.get("mid", {}))
    if not tp_sl_def:
        return (tp_price, sl_price, tp_sl_source)

    _tp_pct = tp_sl_def.get("tp_pct", 0.08)
    _sl_pct = tp_sl_def.get("sl_pct", 0.03)

    _base_atr_pct = 0.01
    _vol_mult = max(0.7, min(3.0, atr_pct / _base_atr_pct)) if atr_pct > 0 else 1.0

    if tier == "long" and _tp_pct == 0 and _sl_pct > 0 and _use_1d_atr_long:
        _atr_1d = ref_price * atr_1d_pct if atr_1d_pct > 0 else ref_price * 0.03
        _mult = LONG_TIER_ATR_1D_MULTIPLIER.get(_band or "mid", 2.0)
        _sl_dist = max(_atr_1d * _mult, ref_price * _sl_pct)
        if action == "buy":
            sl_price = round(ref_price - _sl_dist, 6)
        else:
            sl_price = round(ref_price + _sl_dist, 6)
        tp_price = 0.0
        tp_sl_source = f"{tp_sl_source}+1d_atr_mult={_mult}"
    elif tier == "long" and _tp_pct == 0 and _sl_pct == 0:
        _atr_4h = ref_price * atr_pct * 4 if atr_pct > 0 else ref_price * 0.03
        if _use_band_atr_mult and _band is not None:
            _sl_mult = get_atr_multiplier(_band, "long")
        else:
            _sl_mult = TIER_ATR_MULTIPLIER.get("long", 4.5)
        if action == "buy":
            sl_price = round(ref_price - _atr_4h * _sl_mult, 6)
        else:
            sl_price = round(ref_price + _atr_4h * _sl_mult, 6)
        tp_price = 0.0
        tp_sl_source = "tier_default_atr"
    elif tier == "long" and _tp_pct == 0 and _sl_pct > 0:
        _adj_sl = _sl_pct * _vol_mult
        if action == "buy":
            sl_price = round(ref_price * (1 - _adj_sl), 6)
        else:
            sl_price = round(ref_price * (1 + _adj_sl), 6)
        tp_price = 0.0
        tp_sl_source = f"{tp_sl_source}+pct_only"
    else:
        _adj_tp = _tp_pct * _vol_mult
        _adj_sl = _sl_pct * _vol_mult
        if action == "buy":
            tp_price = round(ref_price * (1 + _adj_tp), 6) if _adj_tp > 0 else 0.0
            sl_price = round(ref_price * (1 - _adj_sl), 6) if _adj_sl > 0 else 0.0
        else:
            tp_price = round(ref_price * (1 - _adj_tp), 6) if _adj_tp > 0 else 0.0
            sl_price = round(ref_price * (1 + _adj_sl), 6) if _adj_sl > 0 else 0.0
        if tp_sl_source == "fixed_fallback":
            tp_sl_source = "tier_default_atr"

    # [2026-09-16 调研轮7] 止损距离**价格层**封顶（mid 2% / long 3%）。
    # 本函数是挂单 TP/SL 价格的权威计算点，`_vol_mult = ATR/1%`（上限 3.0）会把
    # tier 默认 SL 放大 0.7~3.0 倍 —— 实测 mid 3.5%×1.33=**4.67%**、long 6.5%，
    # 而赢家最大逆行 MAE 只有 1.20%（n=18）⇒ avg_loss(-17.10) > avg_win(+13.90)。
    # 入场执行层（midlong_helpers）同款上限**覆盖不到**这里的放大，故必须在此封顶。
    # 回滚：MIDLONG_MAX_SL_PCT_MID / _LONG = 0。
    try:
        from backend.services.mlto.midlong_trade_design import clamp_stop_distance as _clamp_sl
        if sl_price and ref_price > 0:
            _dist = abs(float(sl_price) - float(ref_price)) / float(ref_price)
            _cap_dist, _cap_why = _clamp_sl(_dist, tier)
            if _cap_dist and _cap_dist < _dist:
                if str(action).lower() == "buy":
                    sl_price = round(float(ref_price) * (1 - float(_cap_dist)), 6)
                else:
                    sl_price = round(float(ref_price) * (1 + float(_cap_dist)), 6)
                tp_sl_source = f"{tp_sl_source}+sl_capped@{float(_cap_dist):.2%}"
                logger.info(
                    "[TP/SL] %s %s tier=%s 止损距离封顶 %.2f%%→%.2f%% (%s)",
                    sym or "?", action, tier, _dist * 100, float(_cap_dist) * 100, _cap_why,
                )
    except Exception as _cap_err:  # noqa: BLE001 — 封顶异常不得影响下单
        logger.debug("[TP/SL] 止损距离封顶跳过: %s", _cap_err)

    return (tp_price, sl_price, tp_sl_source)
