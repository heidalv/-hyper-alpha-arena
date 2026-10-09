"""中长线组合风控（P2，2026-07-31）。

虚拟币永续：BTC/ETH/SOL 高度相关，多笔同向 ≈ 一笔大方向赌。
1. 净方向敞口上限（相对权益）
2. 相关簇同向持仓上限
3. 无进展超时离场（持仓过久且峰值未达 0.5R）
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_MIDLONG_NATURES = frozenset({"swing", "trend_follow", "position"})
_MIDLONG_TIERS = frozenset({"mid", "long"})


def _cfg_bool(name: str, default: bool = True) -> bool:
    try:
        from backend.config import settings
        return bool(getattr(settings, name, default))
    except Exception:
        return default


def _cfg_float(name: str, default: float) -> float:
    try:
        from backend.config import settings
        _v = getattr(settings, name, default)
        return default if _v is None else float(_v)
    except Exception:
        return default


def _cfg_int(name: str, default: int) -> int:
    """读整数配置；**仅当值为 None 时**回落默认（保留显式 0）。

    [§55 修复 2026-09-10] 原实现是 `int(getattr(settings, name, default) or default)`，
    会把显式的 0/False 吞成默认值。本模块唯一的调用点是 `MIDLONG_CORR_CLUSTER_MAX`：
    判据 `same_dir >= cap_n` ⇒ 设 0 的语义是「同簇一个都不许开」（最严），
    但原实现会静默变成默认 2（**悄悄放宽两个仓位**）——与 §38.9/§39.3 修过的
    `MIDLONG_MAX_OPEN_POSITIONS` 属同一缺陷类：**配置越严，闸越松**。
    """
    return _cfg_int_allow_zero(name, default)


def _cfg_int_allow_zero(name: str, default: int) -> int:
    """读整数配置但**保留 0**。

    [2026-09-10] `_cfg_int` 用 `or default`，会把显式的 0 吞成默认值——
    于是 `MIDLONG_MAX_OPEN_POSITIONS=0`（约定为"关闭该闸"）实际仍按 4 拦单。
    并发上限的调用点是 `if max_pos > 0 and ...`，语义上 0 必须是 0，故单独提供本函数。
    """
    try:
        from backend.config import settings
        v = getattr(settings, name, default)
        return default if v is None else int(v)
    except Exception:
        return default


def _parse_cluster_symbols() -> List[str]:
    try:
        from backend.config import settings
        raw = getattr(settings, "MIDLONG_CORR_CLUSTER_SYMBOLS", "BTC,ETH,SOL") or "BTC,ETH,SOL"
    except Exception:
        raw = "BTC,ETH,SOL"
    return [s.strip().upper() for s in str(raw).split(",") if s.strip()]


def _is_long_lane_pos(pos: Dict[str, Any]) -> bool:
    """长车道（trend）持仓：nature=trend_follow/position 或 tier=long。"""
    nature = str(pos.get("trade_nature") or "").lower()
    tier = str(pos.get("timeframe_tier") or "").lower()
    return nature in ("trend_follow", "position") or tier == "long"


def _is_midlong_pos(pos: Dict[str, Any]) -> bool:
    nature = str(pos.get("trade_nature") or "").lower()
    tier = str(pos.get("timeframe_tier") or "").lower()
    return nature in _MIDLONG_NATURES or tier in _MIDLONG_TIERS


def _pos_dir(side: Any) -> str:
    s = str(side or "").lower()
    if s in ("long", "buy", "b"):
        return "long"
    if s in ("short", "sell", "s"):
        return "short"
    return ""


def _action_dir(action: str) -> str:
    a = (action or "").lower()
    if a in ("buy", "long"):
        return "long"
    if a in ("sell", "short"):
        return "short"
    return ""


def _notional(pos: Dict[str, Any]) -> float:
    try:
        size = float(pos.get("size") or pos.get("quantity") or 0)
        px = float(
            pos.get("mark_price")
            or pos.get("entry_price")
            or pos.get("current_price")
            or 0
        )
        if size > 0 and px > 0:
            return abs(size * px)
        margin = float(pos.get("margin") or 0)
        lev = float(pos.get("leverage") or 1) or 1
        if margin > 0:
            return abs(margin * lev)
    except Exception:
        return 0.0
    return 0.0


def _equity_from_portfolio(portfolio: Optional[Dict[str, Any]]) -> float:
    if not isinstance(portfolio, dict):
        return 0.0
    bal = portfolio.get("balance") if isinstance(portfolio.get("balance"), dict) else {}
    for k in ("total_equity", "equity", "balance", "available"):
        try:
            v = float(bal.get(k) or portfolio.get(k) or 0)
            if v > 0:
                return v
        except Exception:
            continue
    return 0.0


def collect_midlong_positions(
    portfolio: Optional[Dict[str, Any]] = None,
    positions: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    if positions is not None:
        src = list(positions)
    elif isinstance(portfolio, dict):
        src = list(portfolio.get("positions") or portfolio.get("open_positions") or [])
    else:
        src = []
    return [p for p in src if isinstance(p, dict) and _is_midlong_pos(p)]


def estimate_open_notional(
    *,
    equity: float,
    margin_frac: float,
    # [2026-09-04 币种杠杆] 默认值由 10.0 收到 3.0（=最保守档位）。调用方都应显式传
    # 该币种档位；漏传时按 10x 估算会把名义算大 3 倍多，让组合闸误拦所有开仓。
    leverage: float = 3.0,
    sl_pct: float = 0.0,
    risk_pct: float = 0.01,
) -> float:
    """估计本笔开仓名义（**旧口径/诊断用**，见下）。

    ⚠️ [轮117 2026-09-19 更正] 本条 docstring 曾写"MLTO 的 tranche_margin_pct 是
    「占权益的保证金比例」（如 NIBBLE 0.15、BUILD 0.30）" —— 那句话把**分档系数**与
    **保证金比例**混为一谈，正是"中线被冻结"的病根：`brain.maybe_open` 照它传了
    0.12（保证金占比），而下游按分档系数消费 ⇒ 0.12 × V5 0.25 = 0.030 < 地板 0.05
    ⇒ 每次开仓都被 `[SizeFloor] BLOCK`。
    现行语义（唯一）：`tranche_margin_pct = 分档系数`（占**计划仓位**的比例），
    单一来源 `mlto/tranche_gate.compute_margin_pct()`；保证金"占净值上限"由
    `risk_constitution.MAX_SINGLE_TRADE_MARGIN_PCT` 硬顶负责。
    本函数（equity×margin_frac×leverage）只保留给诊断日志与一键回滚；
    **闸的正式输入**是同口径的 `estimate_open_notional_aligned()`（P12 起）。
    """
    eq = float(equity or 0)
    if eq <= 0:
        return 0.0
    try:
        mf = float(margin_frac)
    except (TypeError, ValueError):
        mf = 0.0
    if mf != mf or mf < 0:  # NaN / neg
        mf = 0.0
    lev = max(1.0, float(leverage or 10.0))
    if 0.0 < mf < 0.99:
        return abs(eq * mf * lev)
    sl = max(float(sl_pct or 0), 0.01)
    rp = float(risk_pct or 0.01)
    mult = 1.0 if mf <= 0 else min(1.0, mf)
    return abs(eq * rp / sl * mult)


def estimate_open_notional_aligned(
    *,
    equity: float,
    sl_pct: float,
    risk_pct: float = 0.0075,
    tranche_mult: float = 1.0,
) -> float:
    """与 `PositionConstruction` **同口径**的名义估计（**P12 执行后已是闸的正式输入**）。

    背景（§60.1 实证）：组合闸原本喂进去的是 `estimate_open_notional()`（`equity×margin×leverage`），
    而真实建仓口径是 `PositionConstruction` 的「按止损距离的风险预算」
    `equity × risk_pct / sl_pct`（× 分档系数）。实测对照（paper 引擎日志）：
    `[Paper][PositionConstruction] VIRTUAL buy lane=mid notional 1188.24→825.31
     caps=['risk_per_trade(0.0075/0.0450)']` —— 与 `equity(~4950)×0.0075/0.045 ≈ 825` 吻合。

    两者在当前配置下差约 **一个数量级**：equity=$4,700、margin=0.15、lev=10 ⇒ 旧式估算
    **$7,050（150% 权益）**，而同口径仅 **$783（16.7%）** ⇒ 组合闸长期"按幻影仓位"拒单
    （22,042 次拦截中 70% 的 `after_pct` 落在 200–500%，无一条 <100%）。

    [P12 执行 2026-09-10] 调用侧（`midlong_helpers`）现按
    `MIDLONG_PORTFOLIO_NOTIONAL_ALIGNED`（默认 **true**）把**闸的输入**换成本函数；
    旧式 `estimate_open_notional()` 保留用于诊断日志与一键回滚（=false）。
    """
    eq = float(equity or 0)
    if eq <= 0:
        return 0.0
    sl = max(float(sl_pct or 0), 0.01)
    rp = max(float(risk_pct or 0), 0.0)
    tf = min(1.0, max(0.0, float(tranche_mult or 1.0))) or 1.0
    return abs(eq * rp / sl * tf)


def concurrent_loss_tripwire(
    *,
    positions: Sequence[Dict[str, Any]],
    equity: float,
    new_notional: float = 0.0,
    new_sl_pct: Optional[float] = None,
    ceiling_pct: float = 2.5,
    default_sl_pct: Optional[float] = None,
) -> Tuple[bool, str]:
    """[续作R17] **风险政策 C**：并发最坏损失跳闸（实测口径）。

    背景：`test_deployed_cap_is_conservative` 的静态不等式
    （并发帽 × 单笔风险上限 ≤ 2.5% 权益）在中线止损 1.5%→3.0% 后被打红
    （8 × 0.15 × 3.0% = 3.6% > 2.5%）。但配置上限 ≠ 实际持仓风险：
    真实持仓各自有实际的止损距离（多数远小于上限）。
    本函数用**实际口径**求和：Σ(每笔名义 × 该笔实际 sl_pct)，加上新仓的
    （新仓 sl_pct 未知时用 default_sl_pct=配置上限，保持保守），
    超过 `ceiling_pct`（默认 2.5%）即拦。
    `sl_pct` 口径 = **百分点**（3.0 = 3%），与 `.env` 的 `MIDLONG_SL_MAX_PCT_*` 一致。
    """
    try:
        equity_f = float(equity or 0)
        ceiling = max(0.0, float(ceiling_pct)) / 100.0
    except (TypeError, ValueError):
        return True, "bad_param"
    if equity_f <= 0:
        return True, "no_equity"
    worst = 0.0
    for p in positions or []:
        try:
            _n = p.get("notional") if isinstance(p, dict) else None
            n = float(_n) if _n is not None else float(_notional(p))
        except Exception:
            n = 0.0
        sl = None
        if isinstance(p, dict):
            try:
                _s = p.get("sl_pct")
                sl = float(_s) / 100.0 if _s is not None else None
            except (TypeError, ValueError):
                sl = None
            if sl is None:
                # 无 sl_pct ⇒ 尝试从价格实测：(entry - sl) / entry
                try:
                    _slp = p.get("sl_price") or p.get("stop_price")
                    _ep = p.get("entry_price") or p.get("avg_entry_price")
                    if _slp and _ep:
                        sl = abs(float(_ep) - float(_slp)) / abs(float(_ep))
                except (TypeError, ValueError):
                    sl = None
        if sl is None:
            # [续作R17] 无任何可测止损数据 ⇒ 该笔按 0 计（fail-open 单仓），每进程告警一次。
            # 绝不回退到配置上限：否则跳闸会退化成"5 仓即拦"的隐性并发帽。
            _w = globals().setdefault("_TRIPWIRE_WARN_NO_SL", {"fired": False})
            if not _w["fired"]:
                _w["fired"] = True
                try:
                    logger.warning(
                        "[Tripwire] 持仓缺 sl_pct/sl_price/entry_price，"
                        "该笔最坏损失按 0 计（fail-open 单仓）"
                    )
                except Exception:
                    pass
            continue
        worst += abs(n) * max(0.0, sl)
    _ns = 0.0
    try:
        _ns = abs(float(new_notional or 0))
    except (TypeError, ValueError):
        _ns = 0.0
    if _ns > 0:
        if new_sl_pct is not None:
            try:
                _nsl = float(new_sl_pct) / 100.0
            except (TypeError, ValueError):
                _nsl = 0.0
        else:
            _nsl = (float(default_sl_pct) / 100.0) if default_sl_pct else 0.0
        worst += _ns * max(0.0, _nsl)
    if worst > equity_f * ceiling:
        return False, (
            f"concurrent_loss_tripwire:{worst / equity_f:.2%}>{ceiling:.2%}"
        )
    return True, "ok"


def check_portfolio_open_allowed(
    *,
    symbol: str,
    action: str,
    portfolio: Optional[Dict[str, Any]] = None,
    positions: Optional[Sequence[Dict[str, Any]]] = None,
    new_notional: float = 0.0,
    max_net_pct: Optional[float] = None,
    is_probe: bool = False,
    long_lane: bool = False,
) -> Tuple[bool, str]:
    """开仓前组合闸：净方向敞口 + 相关簇同向数量。

    [M3 2026-09-14] long_lane=True 时并发帽改用长车道口径
    （MIDLONG_MAX_LONG_LANE_POSITIONS，只数长车道持仓）；净敞口/簇帽不变。
    """
    if not _cfg_bool("MIDLONG_PORTFOLIO_GATE_ENABLED", True):
        return True, "portfolio_gate_off"

    sym = str(symbol or "").upper()
    direction = _action_dir(action)
    if not direction:
        return True, "no_direction"

    mids = collect_midlong_positions(portfolio, positions)
    # [M3/验收轮3 2026-09-14] 并发帽**车道化**：
    #   long_lane=True  → 只数长车道仓 vs MIDLONG_MAX_LONG_LANE_POSITIONS（E1 sleeve）；
    #   long_lane=False → 只数中线仓 vs MIDLONG_MAX_OPEN_POSITIONS。
    # 根因（验收轮3 实测）：E1 5 个长仓把全局帽 4 占满 → 中线 `midlong_open_positions
    # 6>=4` 全部饿死（用户观察「中线几乎没有开仓」）。总敞口仍由下方 net_exposure
    # 检查（全量 midlong）兜底，不因车道化而削弱。
    _cap_count = [p for p in mids if _is_long_lane_pos(p)] if long_lane else [
        p for p in mids if not _is_long_lane_pos(p)
    ]
    equity = _equity_from_portfolio(portfolio)

    # [续作R17] 风险政策 C：并发最坏损失跳闸（实测口径）。
    # 只拦"最坏损失总和 > 2.5% 权益"；开关/上限可配，自身异常时 fail-open（其余闸兜底）。
    if _cfg_bool("MIDLONG_CONCURRENT_LOSS_TRIPWIRE_ENABLED", True):
        try:
            _tw_ok, _tw_why = concurrent_loss_tripwire(
                positions=mids,
                equity=equity,
                new_notional=abs(float(new_notional or 0)),
                new_sl_pct=None,
                ceiling_pct=_cfg_float("MIDLONG_CONCURRENT_LOSS_CEILING_PCT", 2.5),
                default_sl_pct=_cfg_float(
                    "MIDLONG_SL_MAX_PCT_LONG" if long_lane else "MIDLONG_SL_MAX_PCT_MID",
                    0.03,
                ),
            )
            if not _tw_ok:
                return False, _tw_why
        except Exception:
            pass

    # ── 净方向敞口 ──
    signed = 0.0
    for p in mids:
        d = _pos_dir(p.get("side"))
        n = _notional(p)
        if d == "long":
            signed += n
        elif d == "short":
            signed -= n
    add = abs(float(new_notional or 0))
    if direction == "long":
        signed_after = signed + add
    else:
        signed_after = signed - add

    if max_net_pct is not None:
        try:
            cap = float(max_net_pct)
        except (TypeError, ValueError):
            cap = _cfg_float("MIDLONG_MAX_NET_EXPOSURE_PCT", 1.5)
    elif is_probe:
        # 探针可单独更宽，避免首笔试探锁死全通道
        base = _cfg_float("MIDLONG_MAX_NET_EXPOSURE_PCT", 1.5)
        cap = _cfg_float("MIDLONG_NIBBLE_NET_EXPOSURE_PCT", max(base, 2.0))
    else:
        cap = _cfg_float("MIDLONG_MAX_NET_EXPOSURE_PCT", 1.5)

    if equity > 0 and abs(signed_after) / equity > cap:
        before_pct = abs(signed) / equity
        after_pct = abs(signed_after) / equity
        return (
            False,
            f"net_exposure {after_pct:.0%}>{cap:.0%} "
            f"(after {sym} {direction}; before={before_pct:.0%} est=${add:.0f})",
        )

    # ── 相关簇同向上限 ──
    # [验收轮3 2026-09-14] 簇帽计数也车道化（与并发帽同根因）：
    #   long_lane → 只数长车道簇仓 vs MIDLONG_CORR_CLUSTER_MAX_LONG（默认 3，E1 核心宇宙）；
    #   mid      → 只数中线簇仓 vs MIDLONG_CORR_CLUSTER_MAX（默认 2，9/9 山寨齐跌实证）。
    # 旧实现全量计数：E1 持 BTC/ETH 时中线 ETH/SOL 也永远开不出（跨车道误伤）。
    cluster = set(_parse_cluster_symbols())
    if sym in cluster:
        same_dir = 0
        for p in mids:
            ps = str(p.get("symbol") or "").upper()
            if ps not in cluster:
                continue
            if long_lane and not _is_long_lane_pos(p):
                continue
            if (not long_lane) and _is_long_lane_pos(p):
                continue
            if _pos_dir(p.get("side")) == direction:
                same_dir += 1
        cap_n = _cfg_int(
            "MIDLONG_CORR_CLUSTER_MAX_LONG" if long_lane else "MIDLONG_CORR_CLUSTER_MAX",
            3 if long_lane else 2,
        )
        if same_dir >= cap_n:
            return (
                False,
                f"corr_cluster {direction} count={same_dir}>={cap_n} "
                f"({','.join(sorted(cluster))})",
            )

    # ── 每标的并发上限（P13 执行 2026-09-10）──
    # 实证（§61，269 笔已平仓 mid/long）：同标的并发组均值 -2.39%/胜率 27.8%，
    # 单笔组均值 +3.57%/胜率 35.9%，bootstrap 均值差 -5.96%（95%CI [-10.83%, -1.51%]，留一法仍显著）。
    # 口径：只数**同标的同方向**的 mid/long 持仓（对冲仓不算重复暴露）；
    # `MIDLONG_MAX_SAME_SYMBOL_POSITIONS=0` 表示关闭该闸（沿用 _cfg_int_allow_zero 的零语义修复）。
    same_sym_cap = _cfg_int_allow_zero("MIDLONG_MAX_SAME_SYMBOL_POSITIONS", 2)
    if same_sym_cap > 0:
        same_sym_same_dir = sum(
            1 for p in mids
            if str(p.get("symbol") or "").upper() == sym and _pos_dir(p.get("side")) == direction
        )
        if same_sym_same_dir >= same_sym_cap:
            return (
                False,
                f"same_symbol_concurrency {sym} {direction} count={same_sym_same_dir}>={same_sym_cap}"
                " (历史实测该模式均值 -2.4% vs 单笔 +3.6%，§61)",
            )

    # ── 全局中长线并发上限 ──
    # [2026-09-10 第二十八轮] 9/9 夜 5-6 笔同向山寨（0.94x 权益、无对冲）在 alt 齐跌中
    # 单夜 -$155.48（§38）。此闸此前被 `.env` 设为 6 而形同虚设，现已收回代码默认 4。
    # [M3 2026-09-14] 长车道（E1 趋势 sleeve）独立并发帽：E1 有自身车道风险边界
    # （60% 权益桶 + gross/cluster/risk_per_trade 四道帽 + Chandelier 结构止损），
    # 且目标最多 8 个核心币；混在 4 笔全局帽里会让趋势 sleeve 永远开不出仓
    # （2026-09-14 实测：脑 mid 4 仓占满 → E1 全拒）。mid 车道继续走全局帽不变。
    # MIDLONG_MAX_LONG_LANE_POSITIONS 默认 8（=TREND_MAX_POSITIONS）；0=关闭该闸。
    max_pos = _cfg_int_allow_zero("MIDLONG_MAX_OPEN_POSITIONS", 4)
    if long_lane:
        max_pos = _cfg_int_allow_zero("MIDLONG_MAX_LONG_LANE_POSITIONS", 8)
    if max_pos > 0 and len(_cap_count) >= max_pos:
        return (
            False,
            f"midlong_open_positions {len(_cap_count)}>={max_pos}"
            + (f" ({','.join(str(p.get('symbol') or '?') for p in _cap_count[:8])})"),
        )

    return True, "ok"


def choke_point_open_allowed(
    db,
    account_id: int,
    *,
    symbol: str,
    action: str,
    tier: Optional[str] = None,
    trade_nature: Optional[str] = None,
    new_notional: float = 0.0,
    is_probe: bool = False,
) -> Tuple[bool, str]:
    """**下单收口点**组合闸（只对 mid/long 生效，scalp/short 直接放行）。

    [2026-09-10 第 12 轮] 组合闸此前只挂在 `midlong_helpers.try_execute_independent_agent_open`
    一处——覆盖率实测仅 **3/7** 个入口（`_audit_ml/Z54_gate_coverage_map.py`）：
    `trend_e1_engine`、`master_execution` 直下、**加仓路径（`midlong_position_manager`）**、
    `paper_execution` 全部绕过；且加仓能把净敞口绕过（`Z55`：10/289 笔有加仓，最大 +51% 名义）。

    本函数挂在 `paper_engine.place_order`（全仓唯一收口点）上，一次覆盖全部入口；
    与 `midlong_helpers` 的那次检查**幂等**（只读检查，不改变任何状态）。

    语义：
      - 非 mid/long（scalp/short/日内）→ `(True, "not_midlong")`，**不受影响**；
      - 持仓/余额从 DB **实时**读取（收口点优势：不依赖上游快照）；
      - 任何异常 → 放行但 **warning**（与全仓惯例一致，且不静默）。
    """
    _t = str(tier or "").strip().lower()
    _n = str(trade_nature or "").strip().lower()
    if _t not in _MIDLONG_TIERS and _n not in _MIDLONG_NATURES:
        return True, "not_midlong"
    try:
        from backend.database.models import PaperBalance, PaperPosition

        rows = (
            db.query(PaperPosition)
            .filter(PaperPosition.account_id == account_id)
            .filter(PaperPosition.status == "open")
            .all()
        )
        positions = [
            {
                "symbol": getattr(p, "symbol", "") or "",
                "side": getattr(p, "side", "") or "",
                "size": float(getattr(p, "size", 0) or 0),
                "entry_price": float(getattr(p, "entry_price", 0) or 0),
                "mark_price": float(getattr(p, "mark_price", 0) or 0),
                "margin": float(getattr(p, "margin", 0) or 0),
                "trade_nature": getattr(p, "trade_nature", None),
                "timeframe_tier": getattr(p, "timeframe_tier", None),
            }
            for p in rows
        ]
        bal = db.query(PaperBalance).filter(PaperBalance.account_id == account_id).first()
        equity = float(getattr(bal, "total_equity", 0) or 0) if bal else 0.0
        # [§61 观测] 同标的并发开仓在此留痕：近全量历史实测（269 笔已平仓 mid/long）
        # 同标的并发组均值 -2.39%、胜率 27.8%，而单笔组均值 +3.57%、胜率 35.9%，
        # bootstrap 均值差 -5.96%（95%CI [-10.83%, -1.51%]，留一法仍显著）。
        # 本轮**不改判据**（是否加每标的并发上限属决策 P13），先让该模式可被度量。
        try:
            _sym_u = str(symbol or "").upper()
            _same = [p for p in positions if str(p.get("symbol") or "").upper() == _sym_u]
            if _same:
                logger.info(
                    "[MidLongChokeGate] 同标的并发开仓 sym=%s tier=%s nature=%s "
                    "该标的当前已开 %d 笔（历史实测该模式均值 -2.4%% vs 单笔 +3.6%%）",
                    _sym_u, tier, trade_nature, len(_same),
                )
        except Exception:
            pass
        return check_portfolio_open_allowed(
            symbol=symbol,
            action=action,
            portfolio={"balance": {"total_equity": equity}, "positions": positions},
            new_notional=float(new_notional or 0),
            is_probe=is_probe,
            # [M3 2026-09-14] 长车道开仓走长车道并发帽（E1 趋势 sleeve）
            long_lane=(_t in ("long",) or _n in ("trend_follow", "position")),
        )
    except Exception as exc:
        logger.warning(
            "[MidLongChokeGate] 组合闸检查失败(fail-open) sym=%s tier=%s nature=%s: %s",
            symbol, tier, trade_nature, exc,
        )
        return True, f"choke_gate_error:{str(exc)[:60]}"


@dataclass
class NoProgressDecision:
    action: str = "hold"  # hold / close
    reason: str = ""
    peak_r: float = 0.0
    hold_hours: float = 0.0


def _held_hours(position: Dict[str, Any]) -> float:
    try:
        age = position.get("hold_age_hours")
        if age is not None and float(age) >= 0:
            return float(age)
    except Exception:
        pass
    for key in ("opened_at", "created_at", "entry_time", "open_time"):
        val = position.get(key)
        if not val:
            continue
        try:
            if isinstance(val, (int, float)):
                return max(0.0, (time.time() - float(val)) / 3600.0)
            from datetime import datetime, timezone

            dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(
                0.0,
                (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0,
            )
        except Exception:
            continue
    hs = position.get("hold_seconds")
    try:
        if hs is not None:
            return max(0.0, float(hs) / 3600.0)
    except Exception:
        pass
    return 0.0


def _pct_to_fraction(v: Any, *, assume_percent_points: bool = False) -> float:
    """兼容 DB 小数(0.03) 与 get_positions 导出百分数(3.0)。"""
    try:
        x = float(v or 0)
    except (TypeError, ValueError):
        return 0.0
    if assume_percent_points or abs(x) > 1.0:
        return x / 100.0
    return x


def _r_multiple(position: Dict[str, Any]) -> Tuple[float, float]:
    """返回 (peak_R, current_R)；R 以入场到止损的价格距离为单位。"""
    try:
        entry = float(position.get("entry_price") or 0)
        sl = float(position.get("sl_price") or 0)
        side = _pos_dir(position.get("side"))
        if entry <= 0 or sl <= 0 or not side:
            risk = 0.03
        else:
            risk = abs(entry - sl) / entry
            if risk <= 1e-8:
                risk = 0.03

        # paper_engine._position_to_dict 把 peak 乘了 100；原始 ORM/单测用小数
        from_dict = any(
            k in position for k in ("hold_age_hours", "pnl_pct", "peak_unrealized_pnl")
        )
        peak_frac = _pct_to_fraction(
            position.get("peak_pnl_pct"), assume_percent_points=from_dict,
        )

        mark = float(position.get("mark_price") or position.get("current_price") or 0)
        if entry > 0 and mark > 0 and side:
            if side == "long":
                cur_frac = (mark - entry) / entry
            else:
                cur_frac = (entry - mark) / entry
        else:
            lev = float(position.get("leverage") or 1) or 1
            raw = position.get("unrealized_pnl_pct")
            if raw is None:
                raw = position.get("pnl_pct")
                cur_frac = _pct_to_fraction(raw, assume_percent_points=True) / lev
            else:
                cur_frac = _pct_to_fraction(raw, assume_percent_points=from_dict)

        return peak_frac / risk, cur_frac / risk
    except Exception:
        return 0.0, 0.0


def evaluate_no_progress_exit(position: Dict[str, Any]) -> NoProgressDecision:
    """持仓过久且峰值未达阈值 → 主动离场。

    [2026-09-04] 中线 18h + peak_R<0.5 过狠：近 7 天 23 笔 mid 几乎全被
    no_progress 平掉，均亏 43bp；其中多笔平仓时 cur_R>0（价格方向对了，
    只是还没走到 0.5R）。no_progress 的本意是收回**死钱/亏钱**仓位，
    不应砍掉仍在浮盈的单。故：cur_R ≥ 0 时不触发；中线时限 18→36h。
    """
    if not _cfg_bool("MIDLONG_NO_PROGRESS_EXIT_ENABLED", True):
        return NoProgressDecision()
    if not _is_midlong_pos(position):
        return NoProgressDecision()

    tier = str(position.get("timeframe_tier") or "").lower()
    nature = str(position.get("trade_nature") or "").lower()
    if tier == "long" or nature in ("trend_follow", "position"):
        max_h = _cfg_float("MIDLONG_NO_PROGRESS_HOURS_LONG", 72.0)
    else:
        max_h = _cfg_float("MIDLONG_NO_PROGRESS_HOURS_MID", 36.0)

    hold_h = _held_hours(position)
    if hold_h < max_h:
        return NoProgressDecision(hold_hours=hold_h)

    min_peak_r = _cfg_float("MIDLONG_NO_PROGRESS_MIN_PEAK_R", 0.5)
    peak_r, cur_r = _r_multiple(position)
    if peak_r >= min_peak_r:
        return NoProgressDecision(peak_r=peak_r, hold_hours=hold_h)
    # 仍在浮盈：方向对了，只是还没走到目标 R —— 不算"无进展"
    if cur_r >= 0:
        return NoProgressDecision(peak_r=peak_r, hold_hours=hold_h)

    return NoProgressDecision(
        action="close",
        reason=(
            f"[no_progress] hold={hold_h:.1f}h≥{max_h:.0f}h "
            f"peak_R={peak_r:.2f}<{min_peak_r:.2f} cur_R={cur_r:.2f}"
        ),
        peak_r=peak_r,
        hold_hours=hold_h,
    )


def parse_core_basket() -> List[str]:
    try:
        from backend.config import settings
        raw = getattr(settings, "MIDLONG_CORE_BASKET", "") or ""
    except Exception:
        raw = ""
    return [s.strip().upper() for s in str(raw).split(",") if s.strip()]
