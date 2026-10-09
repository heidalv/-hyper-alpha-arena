# -*- coding: utf-8 -*-
"""EdgeGate — P2+P3 入场边际闸（2026-09-29 全面执行）。

依据（docs/P3_造血信号预研_20260929.md / docs/设计可行性测试_20260929.md）：
  三重检验雏形实测（301 笔 mid 入场 × 11 特征）：
    ✅ btc_1d_mom z（t=4.00，OOS t=3.57）——方向配对最强特征
    ✅ funding_z7d（t=1.32，OOS 同向）
    ❌ ret_24h / oi_chg24（OOS 反向，退役）
  联合回放：高 m ∩ price≥$1 ∩ 方向配对 → 每笔 −1.06 → −0.00（盈亏比 0.90→1.18）

规则（仅 tier=mid；long 车道保持 trend_e1 不动）：
  P2 方向配对：z_btc > +Z_BTC_TH 才允许 long；z_btc < −Z_BTC_TH 才允许 short
  P2 标的过滤：entry_price ≥ $1（剔微盘币）
  P2 账面风控：同向开仓数 ≤ MAX_SAME_DIR；当日已实现亏损 ≤ DAILY_LOSS_PCT×权益 → 熔断
  P3 边际分数：m_long = (z_btc + z_funding)/√2 > M_TH 才放行 long
               m_short = |z_btc| > M_TH（数学假设：funding 对空头方向未验证 → 权重 0；
               积攒样本后重估，见 docs/开空研究假设_20260929.md）

fail-open：任何异常 → 放行并记日志（不阻塞现有链路）。
开关：MIDLONG_EDGE_GATE_ENABLED（.env 开启）。回滚：置 false。
"""
from __future__ import annotations

import logging
import math
import os
import time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_KLINES_CACHE: Dict[str, Tuple[float, object]] = {}
_FUNDING_CACHE: Dict[str, Tuple[float, object]] = {}
_CACHE_TTL = 300.0


def _env_f_dotenv(name: str, default: float) -> float:
    """数值开关：os.getenv 取不到则直读 .env（本仓库 .env 键不进 os.environ）。

    [2026-10-09] 实测账户14 权益 488、阈值 1.5% ⇒ 熔断线仅 -7.32，当日已亏 -7.87 即触发
    `midlong_edge_day_loss_halt`，中线当天全天禁开（而该闸只对 mid 生效，长线不受影响
    ⇒ "长线在交易、中线不开"的直接原因之一）。此处让阈值可配且可靠生效。
    """
    import os as _os_g

    v = _os_g.getenv(name)
    if v is None or str(v).strip() == "":
        try:
            from pathlib import Path as _P

            for _c in (_P(__file__).resolve().parents[3] / ".env", _P.cwd() / ".env"):
                if not _c.exists():
                    continue
                for _line in _c.read_text(encoding="utf-8", errors="replace").splitlines():
                    _s = _line.strip()
                    if _s.startswith(name + "="):
                        v = _s.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
    try:
        return float(v if v is not None else default)
    except Exception:
        return float(default)



def _env_b(key: str, default: bool) -> bool:
    try:
        return str(os.getenv(key, "true" if default else "false")).strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        return default


def _env_f(key: str, default: float) -> float:
    try:
        return float(os.getenv(key) or default)
    except (TypeError, ValueError):
        return default


def enabled() -> bool:
    return _env_b("MIDLONG_EDGE_GATE_ENABLED", True)


def _spec() -> Dict[str, float]:
    return dict(
        m_th=_env_f("MIDLONG_EDGE_M_TH", 0.29),
        z_btc_th=_env_f("MIDLONG_EDGE_Z_BTC_TH", 0.5),
        min_price=_env_f("MIDLONG_EDGE_MIN_PRICE", 1.0),
        max_same_dir=int(_env_f("MIDLONG_EDGE_MAX_SAME_DIR", 4.0)),
        daily_loss_pct=_env_f_dotenv("MIDLONG_EDGE_DAILY_LOSS_PCT", 1.5),
    )


def _btc_1d(now: float):
    """BTC 1d 收盘序列（bybit），TTL 缓存。返回 list[(ts, close)]。"""
    key = "btc_1d"
    hit = _KLINES_CACHE.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    return _fetch_btc_1d(key, now)


def _fetch_btc_1d(key: str, now: float):
    try:
        from sqlalchemy import text as _text
        from backend.database.connection import MarketSessionLocal as _M
        q = """
        SELECT timestamp, close_price FROM crypto_klines
        WHERE symbol='BTC' AND exchange='bybit' AND period='1d'
          AND timestamp < :ts ORDER BY timestamp DESC LIMIT 80
        """
        with _M() as s:
            rows = s.execute(_text(q), {"ts": int(now * 1000)}).fetchall()
        out = [(int(r[0]), float(r[1])) for r in rows][::-1]  # crypto_klines.timestamp 为秒
        _KLINES_CACHE[key] = (now, out)
        return out
    except Exception as exc:
        logger.warning("[EdgeGate] BTC 1d 读取失败(fail-open): %s", exc)
        return []


def z_btc_mom(now: float) -> Optional[float]:
    """BTC 1d 收盘/5 日前 −1 的 z 分数（用近 40 日收益分布）。"""
    rows = _btc_1d(now)
    if len(rows) < 12:
        return None
    closes = [c for _, c in rows]
    i = -1
    while i >= -len(closes):
        if rows[i][0] <= now:
            break
        i -= 1
    if len(rows) + i < 6:   # 需要 i 与 i−5 两个点（负索引）
        return None
    ret = closes[i] / closes[i - 5] - 1.0
    _end = len(closes) + i + 1            # 有效数据长度（含 i）
    _start = max(1, _end - 40)            # 近 40 个日收益
    hist = [closes[k] / closes[k - 1] - 1.0 for k in range(_start, _end)]
    if len(hist) < 10:
        return None
    mu = sum(hist) / len(hist)
    var = sum((x - mu) ** 2 for x in hist) / max(len(hist) - 1, 1)
    sd = var ** 0.5
    if sd <= 0:
        return None
    return (ret - mu) / sd


def funding_z(symbol: str, now: float) -> Optional[float]:
    """funding 8h 费率 7d 滚动 z（asof now）。symbol 大写。"""
    key = f"fund_{symbol.upper()}"
    hit = _FUNDING_CACHE.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    try:
        rows = _funding_rows(symbol.upper(), now)
        if len(rows) < 12:
            _FUNDING_CACHE[key] = (now, None)
            return None
        rates = [float(r) for r in rows]
        last = rates[0]
        hist = rates[1:22]
        if len(hist) < 10:
            _FUNDING_CACHE[key] = (now, None)
            return None
        mu = sum(hist) / len(hist)
        var = sum((x - mu) ** 2 for x in hist) / max(len(hist) - 1, 1)
        sd = var ** 0.5
        # ── [2026-10-02 修复 · 「中线从不开仓」主因之一] 退化序列必须当作"无数据" ──
        # 实测（alpha_market.perp_funding，2026-10-02）：hyperliquid 源对 ETH/BTC/SOL/UNI/XRP/
        # VIRTUAL/ADA/XPL/ASTER **写的是同一条 funding 序列**（同 timestamp 同 rate，
        # 例 0.0000125），而 `_funding_rows` 取"第一个返回 ≥12 行的交易所" ⇒ 7 个币拿到**同一个**
        # z_fund=-0.9759。于是 m_long=(z_btc+z_fund)/√2 被钉死为负（当前 zb≈+0.85 ⇒ m≈-0.09），
        # 中线多头**全军覆没**于 `midlong_edge_m_block`（实测占 skip-open 拒因 28%）。
        # 判定与后果：sd≈0（序列恒定=零信息）或 |z| 离谱（sd 极小导致爆炸，曾出 -8e13）⇒ 返回 None；
        # None 的语义就是"无数据"，调用点（:272-274）按既有 fail-open 放行 —— 与
        # btc_1d_mom 无数据、funding 读取异常等分支完全一致，不新增任何开关。
        if sd <= max(1e-12, abs(mu) * 1e-9):
            logger.info(
                "[EdgeGate] funding 序列退化(sd≈0 ⇒ 零信息) sym=%s n=%d last=%s mu=%s sd=%s → 视为无数据(fail-open)",
                symbol, len(hist), last, mu, sd,
            )
            _FUNDING_CACHE[key] = (now, None)
            return None
        z = (last - mu) / sd
        if (not math.isfinite(z)) or abs(z) > 10.0:
            logger.info(
                "[EdgeGate] funding z 异常(z=%s, sym=%s, sd=%s) → 视为无数据(fail-open)",
                z, symbol, sd,
            )
            z = None
        _FUNDING_CACHE[key] = (now, z)
        return z
    except Exception as exc:
        logger.warning("[EdgeGate] funding 读取失败(fail-open): %s", exc)
        return None


def _funding_rows(symbol: str, now: float):
    from sqlalchemy import text as _text
    from backend.database.connection import MarketSessionLocal as _M
    q = """
    SELECT funding_rate FROM perp_funding
    WHERE symbol=:sym AND exchange=:ex AND timestamp < :ts
    ORDER BY timestamp DESC LIMIT 30
    """
    for ex in ("bybit", "okx", "binance", "hyperliquid", "asterdex"):
        try:
            with _M() as s:
                rows = s.execute(_text(q), {"sym": symbol, "ex": ex, "ts": int(now * 1000)}).fetchall()
            if len(rows) >= 12:
                return [float(x[0]) for x in rows]
        except Exception:
            continue
    return []


def _core_rows(q: str, params: dict):
    """核心库（alpha_arena）查询：RLS 管理开关 + 参数化。"""
    from sqlalchemy import text as _text
    from backend.database.connection import SessionLocal as _S
    with _S() as s:
        s.execute(_text("SET app.is_admin='on'"))
        return s.execute(_text(q), params).fetchall()


def _open_same_dir(account_id: int, side: str, tier: str = "mid") -> Optional[int]:
    try:
        side_s = "long" if str(side).lower() in ("long", "buy") else "short"
        rows = _core_rows(
            "SELECT count(*) AS n FROM paper_positions "
            "WHERE account_id=:a AND status='open' AND timeframe_tier=:t AND side=:s",
            {"a": int(account_id), "t": tier, "s": side_s},
        )
        return int(rows[0][0]) if rows else 0
    except Exception as exc:
        logger.debug("[EdgeGate] 同向仓计数失败(fail-open): %s", exc)
        return None


def _day_pnl_breach(account_id: int, spec: Dict[str, float]) -> Tuple[Optional[bool], str]:
    """当日已实现亏损是否触发熔断。返回 (breach|None, reason)。"""
    try:
        rows = _core_rows(
            "SELECT coalesce(sum(unrealized_pnl - coalesce(final_fee_paid,0)),0) AS day_pnl "
            "FROM paper_positions WHERE account_id=:a AND status='closed' "
            "AND closed_at >= date_trunc('day', now())",
            {"a": int(account_id)},
        )
        day_pnl = float(rows[0][0]) if rows else 0.0
        rows2 = _core_rows(
            "SELECT total_equity FROM paper_balances WHERE account_id=:a ORDER BY id DESC LIMIT 1",
            {"a": int(account_id)},
        )
        equity = float(rows2[0][0]) if rows2 and rows2[0][0] else None
        if equity is None or equity <= 0:
            return None, "no_equity"
        limit = -spec["daily_loss_pct"] / 100.0 * equity
        if day_pnl <= limit:
            return True, f"day_loss_{day_pnl:.1f}<=limit_{limit:.1f}"
        return False, f"day_pnl_{day_pnl:.1f}"
    except Exception as exc:
        logger.debug("[EdgeGate] 日亏查询失败(fail-open): %s", exc)
        return None, f"err:{str(exc)[:40]}"


def check_edge_entry(
    account_id: Optional[int],
    symbol: str,
    side: str,
    tier: str,
    entry_price: Optional[float] = None,
) -> Tuple[bool, str]:
    """P2+P3 入场边际闸。仅作用于 tier=mid；异常 fail-open。"""
    if not enabled():
        return True, ""
    if str(tier or "").strip().lower() != "mid":
        return True, ""
    spec = _spec()
    side_s = str(side or "").lower()
    if side_s not in ("long", "buy", "short", "sell"):
        return True, ""
    now = time.time()

    # P2 标的过滤：价格 ≥ $1
    try:
        px = float(entry_price or 0)
        if px > 0 and px < spec["min_price"]:
            return False, (
                f"midlong_edge_price_block: ${px:.4f}<${spec['min_price']:.0f}（剔微盘币，"
                f"联合回放 −1.06→−0.59/笔）"
            )
    except (TypeError, ValueError):
        pass

    # P2 方向配对 + P3 边际分数
    zb = z_btc_mom(now)
    if zb is None:
        return True, "midlong_edge_btc_no_data(fail-open)"
    is_long = side_s in ("long", "buy")
    # ── [2026-09-29 数据工厂] 死区探针：模拟账户在 ±Z_TH 死区内按方向符号开缩仓探针
    #    数据工厂原则（用户口径：模拟盘不因怕亏停产）——死区样本正是后续重标定
    #    死区阈值/空头参数所需的原料。live 账户不受影响。reason 带 `paper_probe×`
    #    前缀，midlong_executor 按既有机制乘 margin（默认 0.25）。
    try:
        _dz_probe_on = _env_b("MIDLONG_EDGE_DEADZONE_PROBE", True)
        if _dz_probe_on and abs(zb) < spec["z_btc_th"]:
            from backend.services.risk_management.loss_lock_policy import loss_locks_disabled as _lld_dz
            if bool(_lld_dz(account_id)):
                _dir_probe = (1.0 if zb > 0.05 else -1.0) if abs(zb) > 0.05 else None
                if _dir_probe is not None and ((is_long and _dir_probe > 0) or (not is_long and _dir_probe < 0)):
                    return True, (
                        f"paper_probe×0.25: midlong_edge_deadzone_probe("
                        f"z_btc={zb:+.2f}∈死区±{spec['z_btc_th']:.1f})"
                    )
    except Exception as _dz_err:
        logger.debug("[EdgeGate] 死区探针判定跳过(fail-open): %s", _dz_err)
    if is_long:
        if zb <= spec["z_btc_th"]:
            return False, (
                f"midlong_edge_dir_block: z_btc={zb:+.2f}≤+{spec['z_btc_th']:.1f}，"
                f"BTC 1d 动量不足，多头不表达（OOS t=3.57 最强特征）"
            )
        zf = funding_z(symbol, now)
        if zf is None:
            return True, "midlong_edge_funding_no_data(fail-open)"
        m = (zb + zf) / (2 ** 0.5)
        if m <= spec["m_th"]:
            return False, (
                f"midlong_edge_m_block: m={m:+.2f}≤{spec['m_th']:.2f}"
                f"(z_btc={zb:+.2f} z_fund={zf:+.2f})"
            )
        return True, f"midlong_edge_ok(m={m:+.2f},z_btc={zb:+.2f},z_fund={zf:+.2f})"
    else:
        if zb >= -spec["z_btc_th"]:
            return False, (
                f"midlong_edge_dir_block: z_btc={zb:+.2f}≥−{spec['z_btc_th']:.1f}，"
                f"BTC 1d 动量不弱，空头不表达"
            )
        # 数学假设（docs/开空研究假设_20260929.md）：空头 m_short=|z_btc|，funding 权重 0（未验证）
        m = abs(zb)
        if m <= spec["m_th"]:
            return False, (
                f"midlong_edge_m_short_block: |z_btc|={m:.2f}≤{spec['m_th']:.2f}"
            )
        return True, f"midlong_edge_short_ok(|z_btc|={m:.2f},假设:funding权重0)"


def check_book_guard(account_id: Optional[int], side: str, tier: str = "mid") -> Tuple[bool, str]:
    """账面风控（同向仓数 + 日亏熔断）。在 edge 方向检查之后调用。"""
    if not enabled() or str(tier or "").strip().lower() != "mid":
        return True, ""
    spec = _spec()
    n = _open_same_dir(int(account_id or 0), side, tier)
    if n is not None and n >= spec["max_same_dir"]:
        return False, (
            f"midlong_edge_same_dir_block: 同向已开 {n} 仓 ≥ {spec['max_same_dir']}（账面 regime）"
        )
    breach, reason = _day_pnl_breach(int(account_id or 0), spec)
    if breach:
        return False, f"midlong_edge_day_loss_halt: {reason}"
    return True, f"midlong_edge_book_ok({reason})"
