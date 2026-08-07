"""TP/SL 校验与因子否决 — 从 monolith 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class TpSlGatesHost:
    v3_factor_cache: Dict[str, dict] = field(default_factory=dict)


def build_tp_sl_gates_host(svc) -> TpSlGatesHost:
    return TpSlGatesHost(
        v3_factor_cache=getattr(svc, "_v3_factor_cache", None) or {},
    )


def factor_veto_check(
    db: Session, sym: str, action: str, host: TpSlGatesHost, mode: str = "paper",
) -> Optional[str]:
    try:
        direction_label = None
        conf = 0.0
        strength = 0.0

        cache_hit = host.v3_factor_cache.get(sym.upper()) if hasattr(host, "v3_factor_cache") else None
        if cache_hit and (time.time() - cache_hit.get("ts", 0)) < 900:
            sig = cache_hit.get("signal")
            if sig is not None:
                raw_dir = getattr(sig, "direction", 0) or 0
                try:
                    raw_dir = float(raw_dir)
                except (TypeError, ValueError):
                    raw_dir = 0.0
                direction_label = "long" if raw_dir > 0.2 else ("short" if raw_dir < -0.2 else "neutral")
                conf = float(getattr(sig, "confidence", 0) or 0)
                strength = abs(float(getattr(sig, "strength", 0) or 0))

        if direction_label is None:
            from datetime import datetime as _dt_v
            from backend.database.models import ATASFactorCache

            row = (
                db.query(ATASFactorCache)
                .filter(ATASFactorCache.cache_key == f"{sym}_15m_composite")
                .first()
            )
            if row is None or not isinstance(row.value, dict):
                return None
            if row.expires_at and row.expires_at < _dt_v.utcnow():
                return None
            v = row.value
            direction_label = str(v.get("direction_label") or "neutral")
            conf = float(v.get("confidence") or 0)
            strength = abs(float(v.get("signal_score") or 0))

        if direction_label == "neutral" or conf < 0.55 or strength < 0.5:
            return None

        counter = (
            (action == "buy" and direction_label == "short")
            or (action == "sell" and direction_label == "long")
        )
        if not counter:
            return None
        return (
            f"因子复合信号反向: AI={action} 但因子方向={direction_label} "
            f"(conf={conf:.2f} strength={strength:.2f})"
        )
    except Exception as exc:
        if mode == "live":
            logger.warning(f"[FullAuto] factor veto 检查异常 {sym}: {exc}，Live 环境按 fail-closed 否决")
            return f"因子否决检查异常(fail-closed): {exc}"
        logger.debug(f"[FullAuto] factor veto 检查跳过(Paper fail-open): {exc}")
        return None

def _compute_atr_pct_from_klines(symbol: str, period: str = "1h", count: int = 20) -> float:
    """从 K 线数据自行计算 ATR%（修复 2026-07-21 P0）。

    原 compute_dynamic_min_sl 依赖 market_data_service.get_latest_atr()，
    但 market_data.py 中该对象不存在 → 永远 ImportError → ATR 永远走不通。
    此函数直接从 market_data.get_kline_data() 拉取 K 线，自行计算 ATR。
    """
    try:
        from backend.services.market_data import get_kline_data
        klines = get_kline_data(symbol, period=period, count=count + 1)
        if not klines or len(klines) < 5:
            return 0.0
        highs = [float(k.get("high", 0) or 0) for k in klines]
        lows = [float(k.get("low", 0) or 0) for k in klines]
        closes = [float(k.get("close", 0) or 0) for k in klines]
        if any(h <= 0 for h in highs) or any(l <= 0 for l in lows):
            return 0.0
        # Wilder's ATR
        trs: list[float] = []
        for i in range(1, len(klines)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)
        if not trs:
            return 0.0
        atr_abs = sum(trs) / len(trs)
        last_close = closes[-1]
        if last_close <= 0:
            return 0.0
        return atr_abs / last_close
    except Exception:
        return 0.0


def compute_dynamic_min_sl(
    symbol: str, trade_nature: str, entry_price: float,
    fallback_pct: float = 0.025,
) -> float:
    # 尝试从 K 线数据计算 ATR（修复 2026-07-21：原 market_data_service 不存在）
    atr_pct = _compute_atr_pct_from_klines(symbol, period="1h", count=20)
    if atr_pct > 0:
        dynamic_sl = max(0.03, min(0.20, atr_pct * 3))
        logger.info(
            f"[SL-Dynamic] {symbol} ATR={atr_pct:.4f} ({atr_pct*100:.2f}%), "
            f"SL=ATR×3={dynamic_sl:.4f} ({dynamic_sl*100:.1f}%)")
        return dynamic_sl

    # ATR 不可用时按 trade_nature 查表
    # [2026-07-30 crypto-native] scalp SL 5%→2%（5m crypto SL 不应设到 5%，
    # 否则单笔大亏吃掉多笔盈利）
    _NATURE_SL = {
        "scalp":        0.02,
        "intraday":     0.06,
        "swing":        0.08,
        "position":     0.12,
        "trend_follow": 0.15,
    }
    table_val = _NATURE_SL.get(trade_nature, 0.05)
    result = max(0.05, max(table_val, fallback_pct))
    logger.info(
        f"[SL-Dynamic] {symbol} trade_nature={trade_nature}, "
        f"table_SL={table_val:.0%}, final={result:.0%}")
    return result

def validate_tp_sl_by_nature(
    trade_nature: str, side: str, entry_price: float,
    tp_price, sl_price, symbol: str = "",
) -> tuple:
    if not entry_price or entry_price <= 0:
        return tp_price, sl_price

    is_long = side in ("long", "buy")

    _LIMITS = {
        # [2026-07-31 research] scalp min_tp/min_sl 0.8%→1.2%，对齐 TIER_SHORT + MR floor
        # scalp: max_tp 4%, max_sl 2.5%（限制大亏）
        "scalp":        (0.012, 0.04,  0.012, 0.025),
        "intraday":     (0.01,  0.12,  0.018, 0.08),
        "swing":        (0.02,  0.30,  0.025, 0.12),
        "position":     (0.05,  0.50,  0.030, 0.18),
        "trend_follow": (0.08,  0.80,  0.040, 0.25),
    }
    min_tp, max_tp, min_sl, max_sl = _LIMITS.get(
        trade_nature, (0.02, 0.30, 0.01, 0.12))

    # TP 方向无效时回退到最小安全 TP
    if tp_price and tp_price > 0:
        if is_long and tp_price <= entry_price:
            tp_price = round(entry_price * (1 + min_tp), 6)
            logger.warning(
                f"[TP校验] {symbol} 多头 TP方向无效(≤入场)，"
                f"回退到最小安全TP=${tp_price:.4f} ({min_tp:.1%})")
        elif not is_long and tp_price >= entry_price:
            tp_price = round(entry_price * (1 - min_tp), 6)
            logger.warning(
                f"[TP校验] {symbol} 空头 TP方向无效(≥入场)，"
                f"回退到最小安全TP=${tp_price:.4f} ({min_tp:.1%})")

    # SL 方向无效时回退到最小安全止损（而非清零导致无保护）
    if sl_price and sl_price > 0:
        if is_long and sl_price >= entry_price:
            # ── D7修复: 尝试基于ATR动态计算，而非固定2.5% ──
            _dynamic_min_sl = compute_dynamic_min_sl(symbol, trade_nature, entry_price, min_sl)
            sl_price = round(entry_price * (1 - _dynamic_min_sl), 6)
            logger.warning(
                f"[SL校验] {symbol} 多头 SL方向无效(≥入场)，"
                f"回退到动态安全SL=${sl_price:.4f} ({_dynamic_min_sl:.1%})")
        elif not is_long and sl_price <= entry_price:
            _dynamic_min_sl = compute_dynamic_min_sl(symbol, trade_nature, entry_price, min_sl)
            sl_price = round(entry_price * (1 + _dynamic_min_sl), 6)
            logger.warning(
                f"[SL校验] {symbol} 空头 SL方向无效(≤入场)，"
                f"回退到动态安全SL=${sl_price:.4f} ({_dynamic_min_sl:.1%})")

    is_long = side in ("long", "buy")

    if tp_price and tp_price > 0:
        tp_dist = ((tp_price - entry_price) if is_long
                   else (entry_price - tp_price)) / entry_price
        if tp_dist < min_tp:
            tp_price = round(entry_price * ((1 + min_tp) if is_long
                                            else (1 - min_tp)), 6)
            logger.info(
                f"[TP校验] {symbol}[{trade_nature}] TP距离{tp_dist:.2%}"
                f"<最小{min_tp:.1%}，强制拉远→${tp_price:.4f}")
        elif tp_dist > max_tp:
            tp_price = round(entry_price * ((1 + max_tp) if is_long
                                            else (1 - max_tp)), 6)
            logger.info(
                f"[TP校验] {symbol}[{trade_nature}] TP距离{tp_dist:.2%}"
                f">最大{max_tp:.0%}，限制→${tp_price:.4f}")

    if sl_price and sl_price > 0:
        sl_dist = ((entry_price - sl_price) if is_long
                   else (sl_price - entry_price)) / entry_price
        if sl_dist < min_sl:
            sl_price = round(entry_price * ((1 - min_sl) if is_long
                                            else (1 + min_sl)), 6)
            logger.info(
                f"[SL校验] {symbol}[{trade_nature}] SL距离{sl_dist:.2%}"
                f"<最小{min_sl:.1%}，强制拉远→${sl_price:.4f}")
        elif sl_dist > max_sl:
            sl_price = round(entry_price * ((1 - max_sl) if is_long
                                            else (1 + max_sl)), 6)
            logger.info(
                f"[SL校验] {symbol}[{trade_nature}] SL距离{sl_dist:.2%}"
                f">最大{max_sl:.0%}，限制→${sl_price:.4f}")

    return tp_price, sl_price
