# backend/services/leverage_authority.py
"""单一杠杆权威(根因 2 终态)。

所有路径(主控 + scalp)计算杠杆时只读此表,不再各自硬编码。
废除 _unify 的 max 覆盖(阶段 A 已止血,此处为权威源)。

说明：
- 本表是 tier 维度的 *上限 cap*，不是「按周期固定分配杠杆」。
- 交易所对同一交易对（净仓）只有一套杠杆；短/中/长是本地分仓记账。
- 实际开仓杠杆由动态杠杆等路径算出后，再经本 cap / mental_cap 钳制；
  已有仓时必须跟交易所/本地统一仓杠杆对齐（见 paper_trading_engine._unify_leverage_for_side）。
- 中长线升级禁止另立 3x/5x 等覆盖规则。
"""
from __future__ import annotations

import os
from typing import Any, Iterable, Optional

# tier→杠杆 *上限*（ceiling）。勿把此表理解成固定分配表。
TIER_LEVERAGE_CAP: dict[str, int] = {
    "short": 20,
    "mid": 20,
    "long": 12,
}
DEFAULT_LEVERAGE = 10
MIN_LEVERAGE = 1.0

# ── [2026-09-04 币种杠杆档位] ────────────────────────────────────────────────
# 交易所的杠杆是 **按币种** 的账户级设置（set_leverage(BTC, 5)），同币同方向
# 会合并成一个净头寸——「长线 3x / 短线 10x」这类按周期分配在交易所落不了地，
# 本地记多套杠杆等于记假账。故杠杆改为「一币一档」，风险敞口交由名义价值
# （PositionConstruction 的单币/单笔/簇/gross 四道帽）控制。
#
# 档位依据爆仓距离 ≈ 1/杠杆：5x→约20%、4x→25%、3x→33%。长线 Chandelier 止损
# 实测 5.7%~8%，极端可达 15%，故最低档 3x 仍能让止损先于爆仓触发。
DEFAULT_SYMBOL_LEVERAGE: dict[str, float] = {
    # 主流币：流动性最好、波动最小
    "BTC": 5.0, "ETH": 5.0,
    # 二线主流
    "SOL": 4.0, "BNB": 4.0, "XRP": 4.0, "DOGE": 4.0,
    "LINK": 4.0, "AVAX": 4.0, "ADA": 4.0,
}
# 未列入的币（小币/新币，波动大）统一走这一档
SYMBOL_LEVERAGE_FALLBACK = 3.0

_QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "USD", "PERP")


def normalize_base_symbol(symbol: Any) -> str:
    """把各种交易对写法归一到裸基币名。

    "BTC/USDT:USDT" / "BTC-USDT" / "BTCUSDT" / "btc" → "BTC"
    注意去后缀时要求剩余长度≥2，避免把 "USDT" 本身削成空串。
    """
    s = str(symbol or "").upper().strip()
    if not s:
        return ""
    for sep in ("/", ":", "-", "_"):
        if sep in s:
            s = s.split(sep)[0].strip()
    for suf in _QUOTE_SUFFIXES:
        if s.endswith(suf) and len(s) > len(suf) + 1:
            s = s[: -len(suf)]
            break
    return s.strip()


def _parse_symbol_leverage_map(raw: str) -> dict[str, float]:
    """解析 SYMBOL_LEVERAGE_MAP="BTC:5,ETH:5,SOL:4"。非法项跳过。"""
    out: dict[str, float] = {}
    for part in str(raw or "").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        sym, _, lev = part.partition(":")
        sym = normalize_base_symbol(sym)
        try:
            v = float(lev.strip())
        except (TypeError, ValueError):
            continue
        if sym and v >= MIN_LEVERAGE:
            out[sym] = v
    return out


def symbol_leverage_enabled() -> bool:
    """币种杠杆总开关。关掉即回落到旧的 requested/tier-cap 行为（便于回滚）。"""
    raw = os.getenv("SYMBOL_LEVERAGE_ENABLED")
    if raw is None or str(raw).strip() == "":
        return True
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def symbol_leverage(symbol: Any) -> float:
    """该币种的统一杠杆档位（下单/对齐交易所/存量修正都用这一个值）。

    优先级：SYMBOL_LEVERAGE_MAP 覆盖 > 内置档位表 > SYMBOL_LEVERAGE_DEFAULT。
    """
    base = normalize_base_symbol(symbol)
    override = _parse_symbol_leverage_map(os.getenv("SYMBOL_LEVERAGE_MAP", ""))
    if base and base in override:
        return max(MIN_LEVERAGE, override[base])
    if base and base in DEFAULT_SYMBOL_LEVERAGE:
        return max(MIN_LEVERAGE, DEFAULT_SYMBOL_LEVERAGE[base])
    try:
        fallback = float(os.getenv("SYMBOL_LEVERAGE_DEFAULT", str(SYMBOL_LEVERAGE_FALLBACK)))
    except (TypeError, ValueError):
        fallback = SYMBOL_LEVERAGE_FALLBACK
    return max(MIN_LEVERAGE, fallback)


def extract_existing_symbol_leverage(
    symbol: str | None,
    positions: Optional[Iterable[Any]] = None,
) -> Optional[float]:
    """同币已有仓时返回其杠杆（多腿取 max）；无仓返回 None。

    兼容 paper / HL / CCXT 仓位 dict 或对象字段：
    symbol|coin、leverage（dict.value 或标量）、size|szi|quantity。
    """
    sym = str(symbol or "").upper().strip()
    if not sym or not positions:
        return None
    levs: list[float] = []
    for p in positions:
        try:
            if isinstance(p, dict):
                p_sym = str(p.get("symbol") or p.get("coin") or "").upper().strip()
                raw_lev = p.get("leverage")
                size = abs(float(
                    p.get("size") or p.get("szi") or p.get("quantity") or p.get("qty") or 0
                ))
            else:
                p_sym = str(
                    getattr(p, "symbol", None) or getattr(p, "coin", None) or ""
                ).upper().strip()
                raw_lev = getattr(p, "leverage", None)
                size = abs(float(
                    getattr(p, "size", None)
                    or getattr(p, "szi", None)
                    or getattr(p, "quantity", None)
                    or 0
                ))
            # [2026-08-31 格式容忍] 仓位符号可能是统一格式("ASTER/USDT:USDT")
            # 而决策符号是裸名("ASTER")（或反之）——按裸基币名等价匹配。
            if p_sym != sym and p_sym.split("/")[0].strip() != sym.split("/")[0].strip():
                continue
            if size <= 0 and isinstance(p, dict) and not p.get("leverage"):
                # 无 size 也无 leverage 字段 → 跳过空壳
                continue
            if isinstance(raw_lev, dict):
                lev = float(raw_lev.get("value") or 0)
            else:
                lev = float(raw_lev or 0)
            if lev >= MIN_LEVERAGE:
                levs.append(lev)
        except Exception:
            continue
    if not levs:
        return None
    return max(levs)


def account_tier_leverage_override(account, tier: str | None) -> int | None:
    """账户级三周期杠杆覆盖（tier_overrides[tier].leverage）。非法/缺失返回 None。"""
    if not tier:
        return None
    try:
        to = getattr(account, "tier_overrides", None) or {}
        if not isinstance(to, dict):
            return None
        seg = to.get(tier)
        v = seg.get("leverage") if isinstance(seg, dict) else seg
        if v is None:
            return None
        v = int(float(v))
        return v if 1 <= v <= 125 else None
    except Exception:
        return None


def resolve_leverage(
    tier: str | None,
    requested: float | None = None,
    mental_cap: float | None = None,
    account=None,
    symbol: Any = None,
) -> float:
    """解析最终杠杆 = min(基准, tier_cap?, mental_cap, 账户覆盖)。

    - 基准：传了 symbol 且币种杠杆启用时 = symbol_leverage(symbol)，此时 requested
      **被忽略**——上游的动态杠杆/AI 建议/各种 10x 兜底一律退位，保证同币只有一个
      杠杆值，能和交易所的 set_leverage(symbol, x) 对齐。仓位大小由名义价值控制。
    - 未传 symbol（或开关关闭）时回落旧行为：基准 = requested or DEFAULT_LEVERAGE。
    - tier_cap: TIER_LEVERAGE_CAP[tier]（受 RISK_USE_LEVERAGE_CAP_BY_TIER 门控）
    - mental_cap:心态状态机连亏下调的 cap(respect,不被每 tick 重置 —— 阶段 A)
    - 以上收紧项对币种档位依然生效（都是往安全方向收）。
    """
    if tier is None:
        # tier 未知时用最保守 cap(long=12),不绕过限制。此前用 max(=20)会让遗漏 tier
        # 的调用方跑到 20x、绕过 long 的 12x 上限 —— 安全默认应为收紧而非放宽。
        _cap = min(TIER_LEVERAGE_CAP.values())
    else:
        # 已知 tier 查表;未知 tier 字符串同样回退到最保守 cap(long=12)。
        _cap = TIER_LEVERAGE_CAP.get(tier, min(TIER_LEVERAGE_CAP.values()))

    _base = normalize_base_symbol(symbol) if symbol is not None else ""
    if _base and symbol_leverage_enabled():
        _cand = float(symbol_leverage(_base))
    else:
        _cand = float(requested) if requested is not None else float(DEFAULT_LEVERAGE)
    if mental_cap is not None:
        _cand = min(_cand, float(mental_cap))
    # [2026-08-28 P2] 账户级三周期杠杆覆盖：只收紧（账户配的杠杆就是该周期上限）
    _acct_cap = account_tier_leverage_override(account, tier) if account is not None else None
    if _acct_cap is not None:
        _cand = min(_cand, float(_acct_cap))

    _use_tier_cap = True
    try:
        from backend.config.settings import RISK_USE_LEVERAGE_CAP_BY_TIER
        _use_tier_cap = bool(RISK_USE_LEVERAGE_CAP_BY_TIER)
    except Exception:
        _use_tier_cap = True
    if _use_tier_cap:
        _cand = min(_cand, float(_cap))
    else:
        # 关闭按周期 cap 时仍保留硬安全上限（与交易路径常见 20x 一致）
        _cand = min(_cand, 20.0)
    return max(MIN_LEVERAGE, _cand)
