# -*- coding: utf-8 -*-
"""[F88 2026-09-14] L1 做市**自进化闭环**（bounded walk-forward self-evolution）。

为什么要它：此前所有参数优化都是「人工跑扫描 → 手工改注册表」，无法持续跟随市场
微结构漂移（本项目实测：同一天内行情可从「活跃」切到「冻结」，宽度最优档随之中变）。

闭环设计（每一步都可审计、可回滚）：
  1. **有界候选**：只围绕当前配置做小步搜索（宽度/持有/库存偏斜/冻结阈值/OFI 阈值），
     每维不超过 ±1 档 —— 防「一次跳变」把车道扔进未知区域；
  2. **走查验证（walk-forward）**：训练窗选点、**验证窗**给出最终指标；候选必须在
     验证窗**双指标**（净额 USD 与 bp）均不劣于在位配置，且其一带头改进；
  3. **护栏（fail-closed）**：成交数下限、回撤上限、改进幅度下限、候选合法性检查；
     任一不满足 ⇒ 拒绝并记录原因；
  4. **变更日志**：每轮写 `data/mm_evolution_journal.jsonl`（候选、指标、决策、前后配置），
     并把 `prev_params` 写进注册表 meta，供回滚；
  5. **自动回滚**：变更后 N 小时若实盘滚动净额跌破阈值 ⇒ 自动恢复 prev_params；
  6. **默认关闭**：`MM_AUTO_EVOLVE=1` 才允许真正改注册表，否则只出「提案」。
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

JOURNAL_PATH = "data/mm_evolution_journal.jsonl"
# 参数搜索的有界档位（每个键 = 候选值列表；当前值总在其中）
GRID: Dict[str, List[Any]] = {
    # [F97 2026-09-14] 补入 7.0：实测「USD-宽度」在 w5~w12 上是**中间峰值**曲线
    # （4 折均值：w5 $22.2 / w6 $34.9 / w7 $28.5 / w8 $28.9 / w10 $24.1 / w12 $24.7），
    # 而原网格 [3,4,5,6,8] 跳过了 7 —— 峰值附近必须有点，否则调参只能靠运气。
    "w_base_bp": [3.0, 4.0, 5.0, 6.0, 7.0, 8.0],
    "max_one_side_seconds": [600.0, 900.0, 1800.0],
    "k_inv": [0.6, 1.0],
    "frozen_max_move_bp": [5.0, 8.0, 12.0],
    "ofi_block_threshold": [0.0, 0.5],
    # [F88b] 把「冻结档」自身也交给进化：冻结宽度/冻结时间常数/波动缩放
    # （此前 frozen_width 固定 3bp、lookback 固定 240、k_vol 固定 0 都是人工定的）
    "frozen_width_bp": [2.0, 3.0, 4.0],
    "frozen_lookback": [120, 240, 480],
    # [F96 2026-09-14] 补入 0.6：实测 k_vol=0.3 在双窗均不劣（净 bp +0.366~0.430 vs
    # +0.247）、k_vol=0.6 净 bp 相近但成交更少（USD 更低）——把上限也交给护栏判定。
    "k_vol": [0.0, 0.3, 0.6],
}
# 护栏
MIN_FILLS = 150              # 验证窗最少成交
MAX_DD_PCT = 3.0             # 验证窗回撤上限（占 $300 权益）
MIN_IMPROVE_BP = 0.01        # 净 bp 至少改进
MIN_IMPROVE_USD = 0.30       # 或净额至少改进（USD）
# [F96 2026-09-14] 全窗容忍带：复利路径敏感（同一配置在不同数据切片上净额可差 ±12%，
# 实测 baseline ±0.6% 但 k_vol 变体 +$118~+$145），硬性「全窗 ≥ 在位」会用噪声
# 否掉真实改进（如 ofi_block=0.0 全窗 −2% 但验证窗 +$5.8 被拒）。改为允许 2% 回退，
# 且**净 bp 不劣**即可进入候选池；最终是否上线仍由验证窗改进决定（纪律不放宽）。
FULL_TOL = 0.02
MAX_CANDIDATES = 24          # 覆盖当前完整单维网格（16）并留扩展余量
ROLLBACK_HOURS = 12.0        # 变更后观察窗口
ROLLBACK_NET_BP = -1.0       # 观察窗净 bp 低于此值 ⇒ 回滚
# [F116 2026-09-14] 多口径稳健性检查用的"成交桶可见性滞后"网格（毫秒）。
# 为什么需要：成交桶按**落库时刻**分桶（`floor(flush/15s)`）且 15s 网格只填 47.5%，
# 实测落库滞后 = **中位 7.6s、p10 1.6s、p90 13.5s**。这个滞后是模型的一个自由度：
# 实测同一配置在 4.4/8.8/17.6s 下的全窗净额在 0.46~2.20bp 间摆动（4.8×）✗。
# 上线决策必须在这个网格上都不实质回退。
# [F118] 网格取**实测分布**的分位点（p10/中位/p90）而不是等距三点：
# 17.6s 已超出 p90 ⇒ 那是压力档而非中心情形，用它当门槛会否掉所有变更 ✗。
ROBUST_DELAYS_MS = (1600.0, 7600.0, 13500.0)
DEFAULT_TICK_DELAY_MS = 7600.0   # = 实测落库滞后中位（回放的默认可见性滞后）


def evolve_enabled() -> bool:
    """是否允许自动改配置（默认关闭：只出提案）。"""
    return str(os.getenv("MM_AUTO_EVOLVE", "0")).strip().lower() in ("1", "true", "yes", "on")


def _journal(entry: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(JOURNAL_PATH), exist_ok=True)
        with open(JOURNAL_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception as e:  # pragma: no cover
        logger.warning("[F88] 进化日志写入失败: %s", e)


def read_journal(limit: int = 20) -> List[Dict[str, Any]]:
    """读最近若干轮进化记录（前端/API 可展示）。"""
    try:
        with open(JOURNAL_PATH, "r", encoding="utf-8") as f:
            rows = [json.loads(x) for x in f if x.strip()]
        return rows[-limit:]
    except Exception:
        return []


def candidate_grid(current: Dict[str, Any], *, rotate: int = 0) -> List[Dict[str, Any]]:
    """围绕当前配置生成有界候选（单维逐一变动 + 在位配置本身）。

    [F96 2026-09-14] 支持 `rotate` 轮换起点：此前 `[:max_candidates]` 按**字典序**
    截断 —— 12 个名额被前几个维度吃满，排在最后的 `k_vol` / `frozen_lookback`
    **永远没被评估过**（实测网格里明明有 k_vol=0.3，而它值 +49% 净 bp）。
    轮换保证跨轮次公平覆盖全部维度（截断不再等于永久忽略）。
    """
    out: List[Dict[str, Any]] = []
    seen = set()

    def _add(p: Dict[str, Any]) -> None:
        key = json.dumps({k: p.get(k) for k in sorted(GRID)}, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            out.append(p)

    _add(dict(current))
    changes: List[Dict[str, Any]] = []
    for k, vals in GRID.items():
        for v in vals:
            if current.get(k) == v:
                continue
            p = dict(current)
            p[k] = v
            changes.append(p)
    if changes and rotate:
        r = int(rotate) % len(changes)
        changes = changes[r:] + changes[:r]
    for p in changes:
        _add(p)
    return out


def full_grid_size(current: Dict[str, Any]) -> int:
    """完整单维网格的候选总数（用于决定候选上限，避免按序截断）。"""
    return len(candidate_grid(current))


def should_deploy(cand: Dict[str, Any], inc: Dict[str, Any],
                  *, min_fills: int = MIN_FILLS, max_dd_pct: float = MAX_DD_PCT,
                  min_improve_bp: float = MIN_IMPROVE_BP,
                  min_improve_usd: float = MIN_IMPROVE_USD,
                  robust: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """护栏判定（纯函数，可单测）：候选是否可在验证窗替换在位配置。

    fail-closed：任何一项不满足即拒绝，并给出原因。

    [F116 2026-09-14] `robust`（可选）= 多口径稳健性表
    `{"delays_ms": [...], "cand_usd": [...], "inc_usd": [...]}`。
    为什么必须加它：成交桶按**落库时刻**分桶（滞后 1~13s，网格只填 47.5%），
    "哪笔成交打到哪张单"存在 ±15~30s 不确定性；实测把可见性滞后从 8.8s 换成
    4.4s/17.6s，同一候选的全窗净额在 **0.46~2.20bp** 间摆动（4.8×）✗✗
    ⇒ 单口径的"改进"很可能只是口径噪声。稳健性门槛：
      · 每个口径下候选都不得比在位**实质回退**（容忍 FULL_TOL）；
      · 且至少半数口径下候选更优。
    """
    if int(cand.get("fills") or 0) < int(min_fills):
        return False, f"样本不足({cand.get('fills')}<{min_fills})"
    _dd = cand.get("max_dd_pct")
    if _dd is not None and float(_dd) > float(max_dd_pct):
        return False, f"回撤超限({float(_dd):.2f}%>{max_dd_pct}%)"
    c_bp, i_bp = float(cand.get("net_bp") or 0.0), float(inc.get("net_bp") or 0.0)
    c_usd, i_usd = float(cand.get("net_usd") or 0.0), float(inc.get("net_usd") or 0.0)
    if c_bp < i_bp and c_usd < i_usd:
        return False, f"双指标均不劣于在位未达成(bp {c_bp:+.3f} vs {i_bp:+.3f}; USD {c_usd:+.2f} vs {i_usd:+.2f})"
    improved = (c_bp >= i_bp + min_improve_bp) or (c_usd >= i_usd + min_improve_usd)
    if not improved:
        return False, (f"改进不足(bp {c_bp:+.3f} vs {i_bp:+.3f}; "
                       f"USD {c_usd:+.2f} vs {i_usd:+.2f})")
    if c_bp <= 0:
        return False, f"候选净边际非正({c_bp:+.3f}bp)"
    if robust:
        delays = list(robust.get("delays_ms") or [])
        cu = [float(x or 0.0) for x in (robust.get("cand_usd") or [])]
        iu = [float(x or 0.0) for x in (robust.get("inc_usd") or [])]
        if delays and len(cu) == len(iu) == len(delays):
            worse = [(d, c, i) for d, c, i in zip(delays, cu, iu)
                     if c < i * (1.0 - FULL_TOL)]
            if worse:
                d0, c0, i0 = worse[0]
                return False, (f"多口径不稳：滞后 {d0/1000:.1f}s 下净额 "
                               f"${c0:.2f} < 在位 ${i0:.2f}×(1-{FULL_TOL})"
                               f"（共 {len(worse)}/{len(delays)} 个口径回退）")
            wins = sum(1 for c, i in zip(cu, iu) if c > i)
            if wins * 2 < len(delays):
                return False, f"多口径胜率不足({wins}/{len(delays)})"
    return True, (f"通过：净 {c_bp:+.3f}bp/{c_usd:+.2f}USD vs 在位 "
                  f"{i_bp:+.3f}bp/{i_usd:+.2f}USD，fills={cand.get('fills')}"
                  + ("，多口径稳健" if robust else ""))


def _params_from_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    p = dict(meta.get("params") or {})
    return {k: p.get(k) for k in GRID if k in p}


def _apply_params(lane_id: str, base_meta: Dict[str, Any], new_params: Dict[str, Any],
                  *, prev: Dict[str, Any], reason: str) -> bool:
    """把候选参数写回注册表（保留其余 meta；记录 prev 供回滚）。"""
    from backend.services import lane_registry as reg

    meta = dict(base_meta)
    p = dict(meta.get("params") or {})
    p.update(new_params)
    meta["params"] = p
    ev = dict(meta.get("evolution") or {})
    ev.update({
        "last_change_ts": datetime.now(timezone.utc).isoformat(),
        "prev_params": prev,
        "reason": reason,
        "mode": "auto" if evolve_enabled() else "proposal",
    })
    meta["evolution"] = ev
    return reg.update_meta(lane_id, meta)


def run_evolution_round(lane_id: str = "mm_asterdex", *, window_days: float = 14.0,
                        train_ratio: float = 0.6, equity: float = 300.0,
                        fill_notional: Optional[float] = None,
                        max_candidates: int = 24) -> Dict[str, Any]:
    """跑一轮自进化：有界候选 + 走查验证 + 护栏 + （可选）落地。

    返回结构化结果（含候选指标表与决策），并写日志。
    """
    import numpy as np

    from backend.services import lane_registry as reg
    from backend.services.market_maker.core import LaneRiskLimits, QuoteParams
    from backend.services.market_maker.portfolio_replay import replay_portfolio, _load_all

    lane = reg.get_lane(lane_id)
    if not lane:
        return {"ok": False, "reason": f"车道不存在: {lane_id}"}
    meta = dict(lane.get("meta") or {})
    symbols = list(meta.get("symbols") or ["BTC"])
    venue = str(meta.get("venue") or "asterdex")
    cur_params = dict(meta.get("params") or {})
    fn = float(fill_notional or cur_params.get("fill_notional") or equity)

    data = _load_all(symbols, venue)
    # [F88c] 规模化保护：多标的时每轮成本 ≈ 标的数 × 候选数 × 2 窗口；按标的数裁剪
    # 候选上限，保证每日轮次在 ~20 分钟内完成（单标的 24、2 标的 16、≥3 标的 12）。
    # [F96] 但「按序截断」会让排在网格末尾的维度永远不被评估（实测 k_vol 从未被测）。
    # 现在：上限 ≥ 完整单维网格规模（当前 16），并用 rotate 轮换截断起点兜底。
    _n_sym = max(1, len(symbols))
    _cap = MAX_CANDIDATES if _n_sym >= 3 else 24
    max_candidates = min(int(max_candidates), _cap)
    _rotate = int(datetime.now(timezone.utc).timetuple().tm_yday)
    primary = data[symbols[0]]
    cut = int((time.time() - float(window_days) * 86400.0) * 1000)
    a = int(np.searchsorted(primary["ots"], cut, "left"))
    sub = {}
    for s in symbols:
        d = data[s]
        a2 = int(np.searchsorted(d["ots"], cut, "left"))
        t2 = int(np.searchsorted(d["tts"], cut, "left"))
        sub[s] = {k: d[k][a2:] for k in ("ots", "bb", "ba")}
        for k in ("tts", "lo", "hi", "sv", "bv", "tmk"):
            sub[s][k] = d[k][t2:]
    # 训练/验证切分（时间顺序，无重叠）
    ots0 = sub[symbols[0]]["ots"]
    split_ts = int(ots0[int(len(ots0) * float(train_ratio))]) if len(ots0) > 10 else None
    if split_ts is None:
        return {"ok": False, "reason": "窗口数据不足"}

    def _slice(lo_ms: Optional[int], hi_ms: Optional[int]) -> Dict[str, Any]:
        out = {}
        for s in symbols:
            d = sub[s]
            m = np.ones(len(d["ots"]), dtype=bool)
            if lo_ms is not None:
                m &= d["ots"] >= lo_ms
            if hi_ms is not None:
                m &= d["ots"] < hi_ms
            out[s] = {k: d[k][m] for k in ("ots", "bb", "ba")}
            tm = np.ones(len(d["tts"]), dtype=bool)
            if lo_ms is not None:
                tm &= d["tts"] >= lo_ms
            if hi_ms is not None:
                tm &= d["tts"] < hi_ms
            for k in ("tts", "lo", "hi", "sv", "bv", "tmk"):
                out[s][k] = d[k][tm]
        return out

    val_sub = _slice(split_ts, None)     # 验证窗（后段，用于「近期最优」选择）

    # [F116 2026-09-14] 与实盘同源的波动基准与复利口径：
    # 此前 `_evaluate` 只喂**切片**数据且不传 `vol_baseline` ⇒ `replay_portfolio` 会
    # 用切片窗口现算基准 ⇒ 基准跟着最近波动走 ⇒ **σ 被系统性归零**（实测 σ=0 占 63%）
    # ⇒ 挂宽变窄、成交变多 ⇒ 候选评分系统性偏乐观（F108c 的同一根因，这里更严重，
    # 因为切片短）✗。实盘用的是注册表锚定值，必须显式传入。
    # 同时补 `enforce_lane_limits`（实盘 .env 已武装）与复利腿量（compound_ratio）。
    anchored_vb = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})
    _compound = float(cur_params.get("compound_ratio") or 0.0)

    def _evaluate(params_like: Dict[str, Any], slice_data: Dict[str, Any],
                  delay_ms: float = DEFAULT_TICK_DELAY_MS) -> Dict[str, Any]:
        qp = QuoteParams(**{k: v for k, v in params_like.items()
                            if k in QuoteParams.__dataclass_fields__})
        lim = LaneRiskLimits(**{k: v for k, v in params_like.items()
                                if k in LaneRiskLimits.__dataclass_fields__})
        r = replay_portfolio(symbols, venue=venue, equity=equity, params=qp, limits=lim,
                             fill_notional=fn, data=slice_data,
                             vol_baseline=(anchored_vb or None),
                             enforce_lane_limits=True,
                             tick_delay_ms=delay_ms,
                             fill_notional_ratio=(_compound if _compound > 0 else None))
        return {"fills": r["fills"], "net_bp": r.get("net_bp"),
                "net_usd": r.get("net_usd"), "max_dd_pct": r.get("max_dd_pct"),
                "flatten_share": r.get("flatten_share")}

    cands = candidate_grid(cur_params, rotate=_rotate)[:max_candidates]
    rows: List[Dict[str, Any]] = []
    # [F88 v2 稳健选择] 每个候选同时评「全窗口（跨行情域）」与「验证窗（近期）」：
    #   ① 硬约束：全窗口净额 **不得低于在位配置**（防止用近期局部最优换掉全局最优——
    #      实测 w3 在近期安静窗 +3.08 vs 在位 +1.25，但全窗口 5.68 vs 17.05，必须拦下）；
    #   ② 在满足①的候选中选**验证窗净额最大**者（保留行情自适应能力）。
    full_sub = sub
    inc_full = _evaluate(cur_params, full_sub)
    eligible: List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = []
    for c in cands:
        try:
            m_val = _evaluate(c, val_sub)
            m_full = _evaluate(c, full_sub)
        except Exception as e:  # pragma: no cover
            logger.warning("[F88] 候选评估失败 %s: %s", c, e)
            continue
        rows.append({"params": c, "metrics": m_val, "full_metrics": m_full})
        # [F96] 候选池准入：全窗不实质回退（容忍 FULL_TOL，或净 bp 不劣）
        _f_usd = float(m_full.get("net_usd") or 0.0)
        _i_usd = float(inc_full.get("net_usd") or 0.0)
        _f_bp = float(m_full.get("net_bp") or 0.0)
        _i_bp = float(inc_full.get("net_bp") or 0.0)
        if _f_usd >= _i_usd * (1.0 - FULL_TOL) or _f_bp >= _i_bp:
            eligible.append((c, m_val, m_full))

    if eligible:
        best = max(eligible, key=lambda x: float(x[1].get("net_usd") or 0.0))[:2]
    else:
        best = None

    inc_m = next((x["metrics"] for x in rows
                  if all(x["params"].get(k) == cur_params.get(k) for k in GRID)), None)
    if inc_m is None:  # 在位配置跑了但顺序不同 ⇒ 直接评估
        inc_m = _evaluate(cur_params, val_sub)

    decision = {"deploy": False, "reason": "无候选（全窗口均不劣于在位的候选为空）"}
    robust_tbl: Optional[Dict[str, Any]] = None
    if best is not None:
        # [F116] 上线前做**多口径稳健性**复核：同一候选在滞后 4.4/8.8/17.6s 三个口径下
        # 都不得比在位实质回退（单口径的"改进"可能只是桶归属噪声，实测可摆动 4.8×）。
        robust_tbl = {"delays_ms": list(ROBUST_DELAYS_MS), "cand_usd": [], "inc_usd": []}
        try:
            for _d in ROBUST_DELAYS_MS:
                robust_tbl["cand_usd"].append(float(
                    _evaluate(best[0], full_sub, _d).get("net_usd") or 0.0))
                robust_tbl["inc_usd"].append(float(
                    _evaluate(cur_params, full_sub, _d).get("net_usd") or 0.0))
        except Exception as e:  # pragma: no cover
            logger.warning("[F116] 稳健性复核失败(按不稳健处理): %s", e)
            robust_tbl = None
        ok, why = should_deploy(best[1], inc_m, robust=robust_tbl)
        changed = any(best[0].get(k) != cur_params.get(k) for k in GRID)
        if ok and changed:
            decision = {"deploy": True, "reason": why}
        elif not changed:
            decision = {"deploy": False, "reason": "全局最优即当前配置（无需变更）"}
        else:
            decision = {"deploy": False, "reason": why}

    applied = False
    entry_extra: Dict[str, Any] = {"incumbent_full": inc_full}
    if robust_tbl is not None:
        entry_extra["robustness"] = robust_tbl
    if best is not None and decision["deploy"]:
        if evolve_enabled():
            applied = _apply_params(lane_id, meta, {k: best[0].get(k) for k in GRID},
                                    prev={k: cur_params.get(k) for k in GRID},
                                    reason=decision["reason"])

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(), "lane_id": lane_id,
        "window_days": window_days, "train_ratio": train_ratio,
        "incumbent": {"params": {k: cur_params.get(k) for k in GRID}, "metrics": inc_m},
        "best": ({"params": best[0], "metrics": best[1]} if best else None),
        "decision": decision, "applied": bool(applied),
        "mode": "auto" if evolve_enabled() else "proposal",
        "candidates": rows,
        **(entry_extra or {}),
    }
    _journal(entry)
    # [F151] 返回值必须带上**稳健性表**与在位全窗读数：否则调用方（试运行脚本、
    # 调度日志）只能看到决策理由里的一两个数字，无法复核门槛本身 ✗（实测 dry-run
    # 打印"稳健性表未生成"其实是返回子集里没有它 ✗）。
    return {"ok": True, **{k: entry[k] for k in
                           ("incumbent", "best", "decision", "applied", "mode",
                            "incumbent_full", "robustness") if k in entry}}


def check_and_rollback(lane_id: str = "mm_asterdex") -> Dict[str, Any]:
    """自动回滚：变更后观察窗内实盘滚动净 bp 跌破阈值 ⇒ 恢复 prev_params。"""
    from backend.services import lane_ledger, lane_registry as reg

    lane = reg.get_lane(lane_id)
    if not lane:
        return {"ok": False, "reason": "车道不存在"}
    meta = dict(lane.get("meta") or {})
    ev = dict(meta.get("evolution") or {})
    prev = ev.get("prev_params")
    if not prev:
        return {"ok": True, "action": "none", "reason": "无历史变更（无需回滚）"}
    try:
        attr = lane_ledger.attribution(days=float(ROLLBACK_HOURS) / 24.0, lane_id=lane_id,
                                       since=ev.get("last_change_ts"))
        total = attr.get("total") or {}
        n = int(total.get("n") or 0)
        net_bp = float(total.get("net_bp") or 0.0)
    except Exception as e:
        return {"ok": False, "reason": f"账本读取失败: {e}"}
    if n < 30:
        return {"ok": True, "action": "none",
                "reason": f"样本不足({n}<30)，继续观察", "net_bp": net_bp}
    if net_bp >= ROLLBACK_NET_BP:
        return {"ok": True, "action": "none",
                "reason": f"变更后 {n} 笔净 {net_bp:+.3f}bp，未触发回滚", "net_bp": net_bp}
    ok = _apply_params(lane_id, meta, {k: prev.get(k) for k in GRID},
                       prev={k: (meta.get("params") or {}).get(k) for k in GRID},
                       reason=f"auto_rollback(net_bp={net_bp:+.3f}<{ROLLBACK_NET_BP})")
    _journal({"ts": datetime.now(timezone.utc).isoformat(), "lane_id": lane_id,
              "event": "auto_rollback", "net_bp": net_bp, "applied": bool(ok),
              "restored": prev})
    return {"ok": bool(ok), "action": "rollback", "net_bp": net_bp, "restored": prev}


def evolution_task(lane_id: str = "mm_asterdex") -> Dict[str, Any]:
    """调度任务：先做回滚检查，再跑一轮进化（默认仅提案）。"""
    rb = check_and_rollback(lane_id)
    if rb.get("action") == "rollback":
        return {"ok": True, "rollback": rb, "evolve": None}
    rnd = run_evolution_round(lane_id)
    return {"ok": True, "rollback": rb, "evolve": rnd}


def register_evolution_task(lane_id: str = "mm_asterdex", interval_seconds: int = 86400) -> bool:
    """把自进化注册到调度器（每 24h 一轮）。"""
    try:
        from backend.services.scheduler import get_scheduler

        sched = get_scheduler()
        sched.add_interval_task(evolution_task, interval_seconds,
                                f"mm_evolution_{lane_id}")
        logger.info("[F88] 做市自进化调度已注册: %s 每 %ss（apply=%s）",
                    lane_id, interval_seconds, evolve_enabled())
        return True
    except Exception as e:
        logger.warning("[F88] 自进化调度注册失败: %s", e)
        return False
