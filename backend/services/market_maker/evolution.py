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
    #
    # [F189 2026-09-15] 上限从 8.0 扩到 **16.0**：数据修复后（F171）首次用**干净数据**
    # 重扫宽度，排序与旧口径**相反** —— 旧口径说"越窄越好"（w4 优于 w5），干净口径是
    # **越宽越好直到 w12~16**（4.84h、3 口径：w4 −$15~−25 / w12 −$2.4~−4.6 /
    # w16 −$1.6~−3.4）✗✓。若网格上限仍是 8.0，自进化下一天就会把 w=12 的配置
    # "修回" ≤8 ⇒ 必须同步扩网格，否则人工结论会被自动流程覆盖 ✗。
    "w_base_bp": [3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0, 14.0, 16.0],
    # [F189] 新增维度：**减仓侧宽度下限**。此前它只被人工设定过一次（1.0→2.0），
    # 从未进过搜索网格。干净数据下它是**唯一能把边际从负翻正**的单旋钮：
    # 2.0 → 6.0 使 3 口径净 bp 从 −0.37/−0.5/−0.6 变成 **+0.68/+0.38/+0.31** ✓✓
    # （机制：F132 实测出场腿中位只有 1~2bp 地板价，往返价差被压到 ~3.5bp ✗；
    #  而 F188 实测"能打到挂单的大动之后是顺势延续"⇒ 窄出场 = 直接把边际送掉 ✗）。
    "min_width_reduce_bp": [2.0, 4.0, 6.0, 8.0],
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
MAX_CANDIDATES = 32          # [F189] 由 24 提到 32：网格新增 `min_width_reduce_bp` 且
                             # w_base 上限扩到 16 ⇒ 完整单维网格 25 个候选，上限必须覆盖
                             # 它（否则又回到"按序截断 = 某些维度永久不被评估"的旧缺陷 ✗）
ROLLBACK_HOURS = 12.0        # 变更后观察窗口
ROLLBACK_NET_BP = -1.0       # 观察窗净 bp 低于此值 ⇒ 回滚
# [F307 2026-09-16] 触发回滚所需的最小成交笔数。此前是内联字面量 30；
# 抽成常量是为了让它与 ROLLBACK_NET_BP 一起被审计（阈值与样本量必须成对评估：
# "净 bp 低于 −1" 在 n=30 与 n=300 下的可信度完全不同）。
ROLLBACK_MIN_SAMPLES = 30
# [F307 2026-09-16] 回滚冷却：同一车道的**成功**回滚在此窗口内只允许一次。
# 现场依据：2026-09-16 05:03→06:10Z 连续触发 5 次，把 w_base_bp 在 8/12/30 之间
# 反复改写——回滚推进了 last_change_ts，但新窗口里仍是那几行亏损腿 ⇒ 条件持续成立。
ROLLBACK_COOLDOWN_HOURS = 12.0
# [F180 2026-09-15] 多口径稳健性检查用的"成交桶可见性滞后"网格（毫秒）。
# 语义已随 F171 变化：成交桶改为"**桶结束后才落库**"（修复覆盖率 47.5%→93.3% ✓）⇒
# 实测落库滞后 = **中位 25.1s、p10 18.2s、p90 31.8s、max 38.7s**（此前是 1~13s）。
# 网格取该分布的 p10/中位/p90 ✓（与 F116 的纪律一致：网格必须来自实测分布，
# 取到分布之外会否掉所有变更 ✗）。
ROBUST_DELAYS_MS = (18200.0, 25100.0, 31800.0)
DEFAULT_TICK_DELAY_MS = 30000.0   # ≈ 实测 p90：模型必须"等桶落库后才看得见" ✓


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
    _stored = dict(meta.get("params") or {})
    if float(_stored.get("active_flow_mode") or 0.0) > 0:
        return {"ok": True, "action": "frozen_mm_grid",
                "reason": "主动流已启用，做市报价宽度网格不再搜索"}
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
    # [F189 2026-09-15] 可把窗口起点**钉在数据修复时刻之后**（`MM_EVOLUTION_SINCE`）。
    # 为什么必须提供：F171/F176 之前写入的成交桶是按**落库时刻**分桶的 ✗ ⇒ 用它们
    # 训练/验证，等于继续在**被高估 ~2.5bp 的口径**上调参（F181 实测），而自进化是
    # 会自动改注册表的流程 ⇒ 那个偏差会直接变成真实配置变更 ✗。默认未设 ⇒ 行为不变 ✓。
    _since_env = (os.getenv("MM_EVOLUTION_SINCE") or "").strip()
    if _since_env:
        try:
            _dt = datetime.fromisoformat(_since_env)
            if _dt.tzinfo is None:
                _dt = _dt.replace(tzinfo=timezone.utc)
            _since_ms = int(_dt.timestamp() * 1000)
            if _since_ms > cut:
                logger.info("自进化窗口起点被钉到 %s（%d）", _since_env, _since_ms)
                cut = _since_ms
        except Exception as _e:  # pragma: no cover
            logger.warning("MM_EVOLUTION_SINCE 解析失败(%s): %s", _since_env, _e)
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


def _rollback_applied_scope(meta: Dict[str, Any]) -> Dict[str, Any]:
    """回滚**实际会写入**的键值：`{k: prev[k] for k in GRID if prev.get(k) is not None}`。

    为什么必须单独算一遍：`_apply_params` 用 `p.update(new_params)` 合并，
    而 `new_params` 只取 `{k: prev.get(k) for k in GRID}` —— 即**只有 GRID 里的键**
    会被写。此前日志直接 `"restored": prev`，把 prev 的**全部**键当成"已恢复"打印。
    现场后果（2026-09-16）：`prev_params` 是局部快照（如 `{"w_base_bp": 12.0}`），
    日志却打印它，读者无法分辨"恢复了一个键"还是"恢复了整个网格"。**日志不得
    比实际写入的范围更大**，否则事后追溯会误判配置来源。
    """
    prev = dict(meta.get("evolution") or {}).get("prev_params") or {}
    return {k: prev.get(k) for k in GRID if prev.get(k) is not None}


def _last_rollback_ts(lane_id: str) -> Optional[datetime]:
    """最近一次**成功**的 auto_rollback 时间（读 append-only 日志）。

    用于冷却：同一车道的回滚在 `ROLLBACK_COOLDOWN_HOURS` 内只允许发生一次。
    读日志而非注册表，是因为日志是 append-only 的历史事实，注册表只存最新状态。
    """
    try:
        if not os.path.exists(JOURNAL_PATH):
            return None
        last: Optional[datetime] = None
        with open(JOURNAL_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if (e.get("event") == "auto_rollback" and e.get("applied")
                        and e.get("lane_id") == lane_id and e.get("ts")):
                    try:
                        last = datetime.fromisoformat(str(e["ts"]))
                    except Exception:
                        continue
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return last
    except Exception as e:      # 读日志失败不得阻断回滚判定（fail-open 到"无冷却"）
        logger.debug("[evolution] 读回滚日志失败: %s", e)
        return None


def check_and_rollback(lane_id: str = "mm_asterdex") -> Dict[str, Any]:
    """自动回滚：变更后观察窗内实盘滚动净 bp 跌破阈值 ⇒ 恢复 prev_params。

    三道闸（fail-closed），顺序有意为之：
      1. **总开关只读**：`MM_AUTO_EVOLVE` 关闭 ⇒ 只做判定与报告，绝不写注册表。
         （F253 契约①；`.env` 也写着"0=只出提案"。此前回滚路径不看总开关 ✗）
      2. **null 中止**：`prev_params` 里任一 GRID 键是 None ⇒ 中止回滚。
         F253 现场：全 null 的旧 prev 被写回 ⇒ `w_base_bp=None`、`frozen_max_move=None`
         ⇒ 下一次 runner 重建在 `compute_quote` 直接 TypeError。宁可保持现状。
      3. **冷却**：同一车道的成功回滚在 `ROLLBACK_COOLDOWN_HOURS` 内只允许一次。
    """
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
    if n < ROLLBACK_MIN_SAMPLES:
        return {"ok": True, "action": "none",
                "reason": f"样本不足({n}<{ROLLBACK_MIN_SAMPLES})，继续观察", "net_bp": net_bp}
    if net_bp >= ROLLBACK_NET_BP:
        return {"ok": True, "action": "none",
                "reason": f"变更后 {n} 笔净 {net_bp:+.3f}bp，未触发回滚", "net_bp": net_bp}

    # ── 闸 2：null 中止（F253 契约②）──
    # 放在阈值判定**之后**：只有真的要做动作时才中止，否则会掩盖"样本不足/
    # 未触发"这些诊断结论（把每种情形都报成 aborted 同样是误导）。
    dirty = sorted(k for k in GRID if k in (prev or {}) and prev.get(k) is None)
    if dirty:
        return {"ok": False, "action": "aborted", "net_bp": net_bp, "samples": n,
                "null_keys": dirty,
                "reason": (f"prev_params 含 null（{','.join(dirty)}）⇒ 中止回滚；"
                           "写回 null 会让 runner 重建时 TypeError（F253 现场）")}

    # ── 闸 3：冷却（F307）──
    # 位置同样关键：在阈值判定之后、动作之前。若放在函数开头会短路上面的诊断分支
    # （本实现初版就是这个错误，把"样本不足/未触发"全报成 cooldown）。
    last_rb = _last_rollback_ts(lane_id)
    if last_rb is not None:
        age_h = (datetime.now(timezone.utc) - last_rb).total_seconds() / 3600.0
        if age_h < float(ROLLBACK_COOLDOWN_HOURS):
            return {"ok": True, "action": "cooldown", "net_bp": net_bp, "samples": n,
                    "reason": (f"触发条件已满足（{n} 笔净 {net_bp:+.3f}bp），但距上次"
                               f"回滚 {age_h:.2f}h < {ROLLBACK_COOLDOWN_HOURS}h，"
                               f"冷却中（不重复改配置）"),
                    "last_rollback_ts": last_rb.isoformat()}

    # ── 闸 1：总开关只读（F253 契约①）──
    # 放在这里（而非函数开头）：关闭时仍然把完整判定结论报出来，便于观察；
    # 只是**不写**。这样"提案模式"下也能看到"现在是否会触发回滚"。
    if not evolve_enabled():
        return {"ok": True, "action": "none", "read_only": True,
                "net_bp": net_bp, "samples": n,
                "reason": (f"MM_AUTO_EVOLVE 未开启 ⇒ 只读不写（判定：{n} 笔净 "
                           f"{net_bp:+.3f}bp 已跌破 {ROLLBACK_NET_BP}，"
                           f"开启后才会回滚）")}

    # 只恢复 GRID 内、且 prev 里确实有值的键（与 _apply_params 的合并语义一致）
    restore_scope = _rollback_applied_scope(meta)
    ok = _apply_params(lane_id, meta, {k: prev.get(k) for k in GRID},
                       prev={k: (meta.get("params") or {}).get(k) for k in GRID},
                       reason=f"auto_rollback(net_bp={net_bp:+.3f}<{ROLLBACK_NET_BP})")
    # [F307] `restored` 只报**实际写入范围**（此前直接打印 prev ⇒ 范围虚大）
    _journal({"ts": datetime.now(timezone.utc).isoformat(), "lane_id": lane_id,
              "event": "auto_rollback", "net_bp": net_bp, "applied": bool(ok),
              "samples": n, "restored": restore_scope,
              "restored_keys": sorted(restore_scope.keys()),
              "prev_snapshot_keys": sorted((prev or {}).keys())})
    return {"ok": bool(ok), "action": "rollback", "net_bp": net_bp,
            "samples": n, "restored": restore_scope}


def evolution_task(lane_id: str = "mm_asterdex") -> Dict[str, Any]:
    """调度任务：先做回滚检查，再跑一轮进化（默认仅提案）。"""
    rb = check_and_rollback(lane_id)
    if rb.get("action") == "rollback":
        return {"ok": True, "rollback": rb, "evolve": None}
    rnd = run_evolution_round(lane_id)
    return {"ok": True, "rollback": rb, "evolve": rnd}


# ══════════════════════════════════════════════════════════════════════
# [2026-10-09 进化重挂] ping-pong 参数进化：桶级状态门的**阈值反事实走查**。
#
# 为什么只进化「桶门阈值」而不是所有 PP 旋钮：thin_frac / rest_sec /
# exit_ticks 的反事实无法从往返账还原（账里没有"如果薄量比例不同会不会
# 成交"）；而桶门是**可反事实的** —— 每个 PP 往返都记了入场语境
# （价差档 × 前档量档），候选阈值可以直接在历史往返上重放：
# 「如果当时门开着（min_n=X），哪些来回不会发生」。诚实、可审计。
# 其余旋钮走 self_tuner 白名单提案 → 部署 → 记分板裁决 → 回滚 的既有链路。
# ══════════════════════════════════════════════════════════════════════
PP_JOURNAL_PATH = "data/pp_evolution_journal.jsonl"
PP_MIN_N_GRID = (10.0, 20.0, 40.0, 100.0)
PP_MIN_KEEP_N = 20.0          # 反事实后至少保留的样本
PP_MIN_KEEP_RATIO = 0.5       # 至少保留在位样本的一半
PP_MIN_IMPROVE_BP = 0.01


def _load_pp_roundtrips() -> List[Dict[str, Any]]:
    from pathlib import Path
    path = Path(__file__).resolve().parents[3] / "data" / "flow_roundtrip_log.jsonl"
    rows: List[Dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            if str(r.get("strategy") or "") == "PP" and r.get("y_bp") is not None:
                rows.append(r)
    except Exception as e:  # noqa: BLE001
        logger.warning("[pp-evo] 往返账读取失败: %s", e)
    return rows


def _pp_bucket_of(row: Dict[str, Any]) -> tuple:
    from backend.services.market_maker.pp_situation import bucket_key
    sym = str(row.get("symbol") or "?").upper()
    side = str(row.get("entry_side") or "")
    if side not in ("buy", "sell"):
        side = "buy"
    key = bucket_key(float(row.get("entry_spread_bp") or 0.0),
                     float(row.get("entry_front_usd") or 0.0))
    return (sym, side, key)


def _pp_gate_stats(rows: Sequence[Dict[str, Any]], min_n: float) -> Dict[str, Any]:
    """把候选阈值反事实施加到历史往返上，返回保留子集的统计。"""
    from backend.services.market_maker.pp_situation import bucket_negative
    buckets: Dict[tuple, List[float]] = {}
    for r in rows:
        buckets.setdefault(_pp_bucket_of(r), []).append(float(r["y_bp"]))
    kept = [float(r["y_bp"]) for r in rows
            if not bucket_negative(
                {"n": len(buckets[_pp_bucket_of(r)]),
                 "win_rate": (sum(1 for v in buckets[_pp_bucket_of(r)] if v > 0)
                              / max(1, len(buckets[_pp_bucket_of(r)]))),
                 "avg_win_bp": (sum(v for v in buckets[_pp_bucket_of(r)] if v > 0)
                                / max(1, sum(1 for v in buckets[_pp_bucket_of(r)] if v > 0))),
                 "avg_loss_bp": (sum(v for v in buckets[_pp_bucket_of(r)] if v < 0)
                                 / max(1, sum(1 for v in buckets[_pp_bucket_of(r)] if v < 0)))},
                min_n=min_n)]
    if not kept:
        return {"n": 0, "win_rate": 0.0, "avg_win_bp": 0.0,
                "avg_loss_bp": 0.0, "avg_bp": 0.0}
    wins = [v for v in kept if v > 0]
    losses = [v for v in kept if v < 0]
    return {
        "n": len(kept),
        "win_rate": len(wins) / len(kept),
        "avg_win_bp": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss_bp": sum(losses) / len(losses) if losses else 0.0,
        "avg_bp": sum(kept) / len(kept),
    }


def pp_evolution_round(*, apply: bool = False) -> Dict[str, Any]:
    """PP 桶门阈值走查（默认仅提案；apply=True 且 MM_AUTO_EVOLVE=1 才落库）。"""
    from pathlib import Path
    from backend.services.market_maker.flow_rules import load_learn_params, save_learn_params
    root = Path(__file__).resolve().parents[3]
    rows = _load_pp_roundtrips()
    if len(rows) < PP_MIN_KEEP_N:
        return {"ok": True, "action": "none",
                "reason": f"PP 往返 {len(rows)} < {PP_MIN_KEEP_N}，样本不足"}
    incumbent = float(load_learn_params(root).get("pp_bucket_min_n") or 20.0)
    inc_stats = _pp_gate_stats(rows, incumbent)
    cands = []
    for v in PP_MIN_N_GRID:
        if abs(v - incumbent) < 1e-9:
            continue
        st = _pp_gate_stats(rows, v)
        keep_ok = st["n"] >= max(PP_MIN_KEEP_N, inc_stats["n"] * PP_MIN_KEEP_RATIO)
        cands.append({"min_n": v, "stats": st, "keep_ok": bool(keep_ok)})
    eligible = [c for c in cands if c["keep_ok"]
                and c["stats"]["avg_bp"] >= inc_stats["avg_bp"] + PP_MIN_IMPROVE_BP
                and c["stats"]["avg_bp"] > 0]
    best = None
    if eligible:
        best = max(eligible, key=lambda c: c["stats"]["avg_bp"])
    decision = "deploy" if best is not None else "keep"
    if best is not None and apply and evolve_enabled():
        p = load_learn_params(root)
        p["pp_bucket_min_n"] = float(best["min_n"])
        save_learn_params(root, p)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "incumbent_min_n": incumbent, "incumbent": inc_stats,
        "best": ({"min_n": best["min_n"], "stats": best["stats"]} if best else None),
        "candidates": cands, "decision": decision,
        "applied": bool(best is not None and apply and evolve_enabled()),
        "mode": "auto" if evolve_enabled() else "proposal",
    }
    try:
        with open(PP_JOURNAL_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception as e:  # noqa: BLE001
        logger.warning("[pp-evo] 日志写入失败: %s", e)
    return {"ok": True, **entry}


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
