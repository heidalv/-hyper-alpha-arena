"""排序打分：CS/TS 动量、流动性、composite、explain。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from backend.services.coin_rank.features import _LIQUID_PREF, factor_soft, load_dc_ticker_rows, norm_sym

logger = logging.getLogger(__name__)


@dataclass
class RankResult:
    symbol: str
    composite: float = 0.0
    liquidity: float = 0.0
    cs_momentum: float = 0.0
    ts_momentum: float = 0.0
    vol_regime: float = 0.5
    factor_match: Optional[float] = None
    factor_detail: Dict[str, Any] = field(default_factory=dict)
    trap_soft: float = 0.0
    mtf_confluence: float = 0.5
    gate: str = "pass"  # pass | soft_reject | hard_reject
    explain: List[str] = field(default_factory=list)
    volume_24h: float = 0.0
    change_24h: float = 0.0
    change_1h: float = 0.0
    change_4h: float = 0.0
    price: float = 0.0
    market_source: str = ""
    decay_mult: float = 1.0
    hist_hit_rate: Optional[float] = None
    hist_avg_pnl_24h: Optional[float] = None
    hist_samples: int = 0
    rank: int = 0
    # [2026-09-12 图信号试点] RTGNN 迁移第一阶（零训练）：领先-滞后分 + 动量加速度分
    lead_score: Optional[float] = None
    dm_score: Optional[float] = None
    graph_score: Optional[float] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "rank": self.rank,
            "score": round(self.composite, 4),
            "composite": round(self.composite, 4),
            "liquidity": round(self.liquidity, 4),
            "cs_momentum": round(self.cs_momentum, 4),
            "ts_momentum": round(self.ts_momentum, 4),
            "vol_regime": round(self.vol_regime, 4),
            "factor_match": self.factor_match,
            "factor_detail": self.factor_detail,
            "trap_soft": round(self.trap_soft, 4),
            "mtf_confluence": round(self.mtf_confluence, 4),
            "gate": self.gate,
            "explain": self.explain,
            "volume_24h": self.volume_24h,
            "change_24h": self.change_24h,
            "change_1h": self.change_1h,
            "change_4h": self.change_4h,
            "price": self.price,
            "market_source": self.market_source,
            "decay_mult": self.decay_mult,
            "hist_hit_rate": self.hist_hit_rate,
            "hist_avg_pnl_24h": self.hist_avg_pnl_24h,
            "hist_samples": self.hist_samples,
            "lead_score": round(self.lead_score, 4) if self.lead_score is not None else None,
            "dm_score": round(self.dm_score, 4) if self.dm_score is not None else None,
            "graph_score": round(self.graph_score, 4) if self.graph_score is not None else None,
        }


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _percentile_ranks(values: List[float]) -> List[float]:
    n = len(values)
    if n <= 1:
        return [0.5] * n
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    for r, i in enumerate(order):
        ranks[i] = r / (n - 1)
    return ranks


def _graph_weights() -> Tuple[float, float]:
    """(graph_weight, lead_weight)：settings 优先，缺省 0.10 / 0.60。"""
    try:
        from backend.config.settings import (
            COIN_RANK_GRAPH_LEAD_WEIGHT,
            COIN_RANK_GRAPH_WEIGHT,
        )

        gw = float(COIN_RANK_GRAPH_WEIGHT)
        lw = float(COIN_RANK_GRAPH_LEAD_WEIGHT)
        return max(0.0, min(1.0, gw)), max(0.0, min(1.0, lw))
    except Exception:
        return 0.10, 0.60


def score_rows(
    rows: Dict[str, Dict[str, Any]],
    *,
    symbols: Optional[List[str]] = None,
    apply_factor: bool = True,
    decay_map: Optional[Dict[str, float]] = None,
    hist_map: Optional[Dict[str, Dict[str, Any]]] = None,
    graph_map: Optional[Dict[str, Dict[str, float]]] = None,
) -> List[RankResult]:
    """对 DC rows 打分；可选子集 symbols。

    graph_map（图信号试点）：{SYM: {"lead": float, "dm": float}}，来自
    coin_rank.graph_signal.compute_graph_signals。提供时按 COIN_RANK_GRAPH_WEIGHT
    软融合进 base（与 factor_soft 同模式：base = (1−w)·base + w·graph_comp）。
    """
    gw, lw = _graph_weights()
    if symbols:
        want = {norm_sym(s) for s in symbols if s}
        items = [rows[s] for s in want if s in rows]
        # 缺失的也建空行
        for s in want:
            if s not in rows:
                items.append({"symbol": s, "volume_24h": 0, "change_24h": 0, "price": 0, "sources": ["missing"]})
    else:
        items = list(rows.values())

    if not items:
        return []

    has_volume = any(float(r.get("volume_24h") or 0) > 0 for r in items)
    max_vol = max((float(r.get("volume_24h") or 0) for r in items), default=0.0) or 1.0
    pref_idx = {s: i for i, s in enumerate(_LIQUID_PREF)}

    abs_chgs = [abs(float(r.get("change_24h") or 0)) for r in items]
    cs_ranks = _percentile_ranks(abs_chgs)

    results: List[RankResult] = []
    for i, r in enumerate(items):
        sym = norm_sym(r.get("symbol") or "")
        vol = float(r.get("volume_24h") or 0)
        chg24 = float(r.get("change_24h") or 0)
        chg1 = float(r.get("change_1h") or 0)
        chg4 = float(r.get("change_4h") or 0)
        uni = float(r.get("universe_score") or 0)
        explain: List[str] = []

        if has_volume:
            liq = _clip01(vol / max_vol)
        else:
            liq = _clip01(max(0.25, 1.0 - pref_idx.get(sym, 80) / 80.0))
            explain.append("no_volume_pref")

        cs_mom = cs_ranks[i] if i < len(cs_ranks) else 0.5
        # TS：短周期与 24h 同向则加分
        signs = [chg1, chg4, chg24]
        nonzero = [s for s in signs if abs(s) > 1e-9]
        if len(nonzero) >= 2:
            same = all((s > 0) == (nonzero[0] > 0) for s in nonzero)
            ts_mom = 0.75 if same else 0.35
        else:
            ts_mom = _clip01(abs(chg24) / 15.0)

        vol_regime = _clip01(abs(chg24) / 20.0)
        base = 0.45 * liq + 0.25 * cs_mom + 0.20 * ts_mom + 0.10 * _clip01(uni)
        explain.append(f"liq={liq:.2f}")
        explain.append(f"cs={cs_mom:.2f}")
        explain.append(f"ts={ts_mom:.2f}")

        fm: Optional[float] = None
        fd: Dict[str, Any] = {}
        if apply_factor:
            fm, fd = factor_soft(sym)
            if fm is not None:
                # summarize_exposure 已归一到 [-1,1]，映射到 [0,1] 再软融合（与 auto_coin 一致）
                fm_n = _clip01((float(fm) + 1.0) / 2.0)
                base = 0.65 * base + 0.35 * fm_n
                explain.append(f"factor={fm_n:.2f}")

        # [2026-09-12 图信号试点] lead/dm 软融合（graph_map 缺币时跳过，零影响）
        lead_s: Optional[float] = None
        dm_s: Optional[float] = None
        graph_comp: Optional[float] = None
        if graph_map and sym in graph_map:
            g = graph_map[sym]
            try:
                lead_s = _clip01(float(g.get("lead") or 0.0))
                dm_s = _clip01(float(g.get("dm") or 0.0))
                graph_comp = _clip01(lw * lead_s + (1.0 - lw) * dm_s)
                if gw > 0:
                    base = (1.0 - gw) * base + gw * graph_comp
                explain.append(f"graph={graph_comp:.2f}(lead={lead_s:.2f},dm={dm_s:.2f})")
            except (TypeError, ValueError):
                lead_s = dm_s = graph_comp = None

        # TrapSoft：暴涨但短周期回吐 / 高波动低流动性
        trap = 0.0
        if abs(chg24) >= 12 and abs(chg1) > 0 and (chg24 > 0) != (chg1 > 0):
            trap = max(trap, 0.55)
            explain.append("trap:24h_1h_diverge")
        if abs(chg24) >= 18 and liq < 0.25:
            trap = max(trap, 0.7)
            explain.append("trap:illiquid_spike")
        if abs(chg24) >= 8 and abs(chg4) < 0.5 and abs(chg1) < 0.3:
            trap = max(trap, 0.4)
            explain.append("trap:stale_after_move")

        # MTF confluence：1h/4h/24h 同向
        dirs = []
        for v in (chg1, chg4, chg24):
            if abs(v) >= 0.3:
                dirs.append(1 if v > 0 else -1)
        if len(dirs) >= 2 and len(set(dirs)) == 1:
            mtf = 0.85
        elif len(dirs) >= 2 and len(set(dirs)) > 1:
            mtf = 0.25
            explain.append("mtf_conflict")
        else:
            mtf = 0.5

        decay = 1.0
        hist_hit = None
        hist_pnl = None
        hist_n = 0
        if decay_map and sym in decay_map:
            decay = float(decay_map[sym])
            explain.append(f"decay={decay:.2f}")
        if hist_map and sym in hist_map:
            h = hist_map[sym]
            hist_hit = h.get("hit_rate")
            hist_pnl = h.get("avg_pnl_24h")
            hist_n = int(h.get("samples") or 0)

        composite = _clip01(base * decay)

        results.append(
            RankResult(
                symbol=sym,
                composite=composite,
                liquidity=liq,
                cs_momentum=cs_mom,
                ts_momentum=ts_mom,
                vol_regime=vol_regime,
                factor_match=fm,
                factor_detail=fd if isinstance(fd, dict) else {},
                trap_soft=trap,
                mtf_confluence=mtf,
                explain=explain,
                volume_24h=vol,
                change_24h=chg24,
                change_1h=chg1,
                change_4h=chg4,
                price=float(r.get("price") or 0),
                market_source=",".join(r.get("sources") or []),
                decay_mult=decay,
                hist_hit_rate=hist_hit,
                hist_avg_pnl_24h=hist_pnl,
                hist_samples=hist_n,
                lead_score=lead_s,
                dm_score=dm_s,
                graph_score=graph_comp,
                raw=dict(r),
            )
        )

    results.sort(key=lambda x: x.composite, reverse=True)
    for i, rr in enumerate(results):
        rr.rank = i + 1
    return results
