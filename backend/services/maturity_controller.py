"""
MaturityController — 数据成熟度中枢（门控松紧的单一来源）

设计目标（见「策略门控与OpenCode闭环重构」计划）：
  当前 50+ 门控各自为政、且几乎只会单向收紧，模拟盘早期陷入
  「样本少 → 收紧 → 开仓更少 → 学习数据更少」的恶性循环。

本控制器按「累计已平仓笔数 + 真实胜率」，把每个维度
（全局 / 每 symbol+方向 / 每 nature·tier）划成三阶段，所有门控
统一读它来决定松紧，实现 **双向、数据驱动**：

  - warmup（探索期，样本 < N1）：放宽门槛，鼓励多开仓累积数据。
  - growth（成长期，N1 ≤ 样本 < N2）：门槛线性回归到基准。
  - mature（成熟期，样本 ≥ N2）：按真实胜率在基准附近上下浮动
    （胜率好 → 放松，差 → 收紧）。

N1/N2 口径（激进·快累积，已拍板）：
  - 全局：           N1=20  N2=60
  - 每 symbol+方向： N1=5   N2=15
  - 每 nature·tier： N1=6   N2=20

输出语义 —— `conf_relief`：从「置信度有效门槛」中**减去**的分数：
  - 正值 = 放宽门槛（warmup/growth/胜率好的 mature）
  - 负值 = 收紧门槛（胜率差的 mature）
  - 上限 +15（warmup 最大放宽），下限 -5（mature 最大收紧）

安全护栏：
  - live/真金一律严格：mode="live" 时强制 mature 基准、relief=0。
  - 任何放宽只作用于「软门槛」（置信度/盈亏比下限内），保命硬门
    （日亏上限/回撤冻结/熔断）不读本控制器。

本模块默认 **只读影子输出**：run_maturity_tick() 计算并写
data/maturity_state.json 供观测；是否被门控消费由调用方按阶段接入，
未接入前不改变任何交易行为。
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MATURITY_STATE_FILE = os.path.join("data", "maturity_state.json")
_state_cache: dict = {"ts": 0.0, "data": {}}
_CACHE_TTL = 60.0

# ── N1/N2 阈值口径（激进·快累积，默认值；全局维度可被 runtime_tuning 覆盖） ──
DIMENSION_THRESHOLDS: Dict[str, Dict[str, int]] = {
    "global": {"n1": 20, "n2": 60},
    "symbol_side": {"n1": 5, "n2": 15},
    "nature_tier": {"n1": 6, "n2": 20},
}

# ── 松紧系数边界 ──
MAX_WARMUP_CONF_RELIEF = 15.0   # warmup 最大放宽（置信度门槛 -15），默认值


def _resolve_runtime_thresholds() -> Dict[str, Dict[str, int]]:
    """读取 runtime_tuning 对成熟度旋钮的覆盖（OpenCode 慢循环可调）。

    仅覆盖全局维度的 N1/N2；symbol_side / nature_tier 保持默认。
    """
    dims = {k: dict(v) for k, v in DIMENSION_THRESHOLDS.items()}
    try:
        from backend.services.runtime_tuning_store import get_tuning_int
        n1 = get_tuning_int("maturity_global_n1", dims["global"]["n1"])
        n2 = get_tuning_int("maturity_global_n2", dims["global"]["n2"])
        if n2 <= n1:
            n2 = n1 + 1
        dims["global"]["n1"] = n1
        dims["global"]["n2"] = n2
    except Exception:
        pass
    return dims


def _resolve_max_warmup_relief() -> float:
    try:
        from backend.services.runtime_tuning_store import get_tuning_float
        return float(get_tuning_float("maturity_max_warmup_relief", MAX_WARMUP_CONF_RELIEF))
    except Exception:
        return MAX_WARMUP_CONF_RELIEF
MATURE_MAX_RELIEF = 5.0          # 成熟期胜率好时最多再放宽 -5
MATURE_MAX_TIGHTEN = 5.0         # 成熟期胜率差时最多收紧 +5
MATURE_GOOD_WIN_RATE = 0.55      # 成熟期视为「好」的胜率
MATURE_BAD_WIN_RATE = 0.40       # 成熟期视为「差」的胜率
MIN_MATURE_SAMPLE_FOR_TIGHTEN = 10  # 收紧需要的最小样本（避免小样本误判）

STAGE_WARMUP = "warmup"
STAGE_GROWTH = "growth"
STAGE_MATURE = "mature"


# ══════════════════════════════════════════════════
#  阶段与松紧系数计算
# ══════════════════════════════════════════════════

def _stage(count: int, n1: int, n2: int) -> str:
    if count < n1:
        return STAGE_WARMUP
    if count < n2:
        return STAGE_GROWTH
    return STAGE_MATURE


def _conf_relief(count: int, win_rate: float, n1: int, n2: int, max_relief: float) -> float:
    """单维度松紧系数（正=放宽，负=收紧）。"""
    if count < n1:
        return max_relief
    if count < n2:
        # 成长期：从 MAX 线性回归到 0
        span = max(1, n2 - n1)
        frac = (n2 - count) / span
        return round(max_relief * frac, 1)
    # 成熟期：按真实胜率上下浮动
    if win_rate >= MATURE_GOOD_WIN_RATE:
        return MATURE_MAX_RELIEF
    if win_rate <= MATURE_BAD_WIN_RATE and count >= MIN_MATURE_SAMPLE_FOR_TIGHTEN:
        return -MATURE_MAX_TIGHTEN
    return 0.0


def _dim_state(
    count: int, win_rate: float, dim: str,
    dims: Optional[Dict[str, Dict[str, int]]] = None,
    max_relief: float = MAX_WARMUP_CONF_RELIEF,
) -> Dict[str, Any]:
    th = (dims or DIMENSION_THRESHOLDS)[dim]
    n1, n2 = th["n1"], th["n2"]
    return {
        "stage": _stage(count, n1, n2),
        "count": count,
        "win_rate": round(float(win_rate or 0), 4),
        "conf_relief": _conf_relief(count, win_rate, n1, n2, max_relief),
        "n1": n1,
        "n2": n2,
    }


# ══════════════════════════════════════════════════
#  从 DB 计算成熟度全景
# ══════════════════════════════════════════════════

def compute_maturity_state(
    db=None,
    *,
    account_id: Optional[int] = None,
    mode: str = "paper",
) -> Dict[str, Any]:
    """从已平仓数据（累计，不设时间窗）计算各维度成熟度。

    复用 trade_performance_analyzer 的平仓加载与盈亏估算；
    symbol+方向 维度需要 side，单独聚合。
    """
    from backend.services.trade_performance_analyzer import (
        _load_closed_positions_orm,
        _resolve_pnl,
    )

    own_db = False
    if db is None:
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        own_db = True

    try:
        rows = _load_closed_positions_orm(
            db,
            since_hours=None,
            since_days=None,
            account_id=account_id,
            exclude_rebate=True,
        )
    except Exception as exc:
        logger.warning("[Maturity] 加载平仓数据失败: %s", exc)
        rows = []
    finally:
        if own_db:
            try:
                db.close()
            except Exception:
                pass

    # 读取 runtime_tuning 对成熟度旋钮的覆盖（OpenCode 慢循环可调）
    dims = _resolve_runtime_thresholds()
    max_relief = _resolve_max_warmup_relief()

    # ── 全局 ──
    pnls = [_resolve_pnl(r) for r in rows]
    g_count = len(pnls)
    g_wins = sum(1 for p in pnls if p > 0)
    g_wr = g_wins / g_count if g_count else 0.0

    # ── symbol+方向 / nature·tier 聚合 ──
    sym_buckets: Dict[str, List[float]] = {}
    nt_buckets: Dict[str, List[float]] = {}
    for r in rows:
        pnl = _resolve_pnl(r)
        sym = str(r.get("symbol") or "unknown").strip() or "unknown"
        side = str(r.get("side") or "unknown").strip().lower() or "unknown"
        nature = str(r.get("trade_nature") or "unknown").strip().lower() or "unknown"
        tier = str(r.get("timeframe_tier") or "unknown").strip().lower() or "unknown"
        sym_buckets.setdefault(f"{sym}|{side}", []).append(pnl)
        nt_buckets.setdefault(f"{nature}|{tier}", []).append(pnl)

    def _bucket_states(buckets: Dict[str, List[float]], dim: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key, arr in buckets.items():
            cnt = len(arr)
            wins = sum(1 for p in arr if p > 0)
            out[key] = _dim_state(cnt, wins / cnt if cnt else 0.0, dim, dims, max_relief)
        return out

    state = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "global": _dim_state(g_count, g_wr, "global", dims, max_relief),
        "by_symbol_side": _bucket_states(sym_buckets, "symbol_side"),
        "by_nature_tier": _bucket_states(nt_buckets, "nature_tier"),
        "config": {
            "thresholds": dims,
            "max_warmup_conf_relief": max_relief,
            "mature_good_win_rate": MATURE_GOOD_WIN_RATE,
            "mature_bad_win_rate": MATURE_BAD_WIN_RATE,
        },
    }
    return state


def run_maturity_tick(account_id: Optional[int] = None) -> Dict[str, Any]:
    """计算成熟度并写 data/maturity_state.json（供观测 + 门控消费）。"""
    try:
        state = compute_maturity_state(account_id=account_id, mode="paper")
        os.makedirs(os.path.dirname(MATURITY_STATE_FILE) or "data", exist_ok=True)
        with open(MATURITY_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        _state_cache["ts"] = 0.0  # 强制下次读取刷新
        g = state["global"]
        logger.info(
            "[Maturity] tick: global stage=%s count=%d wr=%.0f%% relief=%+.1f "
            "| symbols=%d nature_tier=%d",
            g["stage"], g["count"], g["win_rate"] * 100, g["conf_relief"],
            len(state["by_symbol_side"]), len(state["by_nature_tier"]),
        )
        return state
    except Exception as exc:
        logger.warning("[Maturity] tick 失败: %s", exc)
        return {}


# ══════════════════════════════════════════════════
#  门控消费入口（带缓存）
# ══════════════════════════════════════════════════

def get_maturity_state(*, max_age: float = _CACHE_TTL) -> Dict[str, Any]:
    """读取成熟度状态（60s 缓存）；文件缺失时即时计算一次。"""
    now = time.time()
    if now - _state_cache["ts"] < max_age and _state_cache["data"]:
        return _state_cache["data"]
    data: Dict[str, Any] = {}
    try:
        if os.path.isfile(MATURITY_STATE_FILE):
            with open(MATURITY_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
    except Exception as exc:
        logger.debug("[Maturity] 状态读取失败: %s", exc)
    if not data:
        data = run_maturity_tick()
    _state_cache["ts"] = now
    _state_cache["data"] = data
    return data


def _stage_rank(stage: str) -> int:
    return {STAGE_WARMUP: 0, STAGE_GROWTH: 1, STAGE_MATURE: 2}.get(stage, 2)


def resolve_relief(
    *,
    symbol: Optional[str] = None,
    side: Optional[str] = None,
    nature: Optional[str] = None,
    tier: Optional[str] = None,
    mode: str = "paper",
) -> Dict[str, Any]:
    """门控调用点：给定一笔候选交易的维度，返回有效松紧系数。

    返回 {relief, stage, driver}:
      - relief: 应从置信度有效门槛中减去的分数（正=放宽，负=收紧）
      - stage:  生效的成熟度阶段
      - driver: 是哪个维度在生效（用于可解释日志）

    组合规则（保守向下、慷慨于 warmup）：
      - 只要有任一相关维度仍处 warmup → 取 warmup 放宽（继续累积数据）；
      - 否则若有 growth → 取其中最大的 growth 放宽；
      - 全部 mature → 取最保守（最小）的 relief（按表现最差维度收紧）。

    live/真金一律严格：强制 mature 基准、relief=0。
    """
    if (mode or "paper").lower() == "live":
        return {"relief": 0.0, "stage": STAGE_MATURE, "driver": "live_strict"}

    state = get_maturity_state()
    _cap = MAX_WARMUP_CONF_RELIEF
    if state:
        try:
            _cap = float((state.get("config") or {}).get("max_warmup_conf_relief", MAX_WARMUP_CONF_RELIEF))
        except Exception:
            _cap = MAX_WARMUP_CONF_RELIEF
    if not state:
        # 无状态：按最宽松 warmup 处理（鼓励累积），但封顶
        return {"relief": _cap, "stage": STAGE_WARMUP, "driver": "no_state"}

    candidates: List[Tuple[str, Dict[str, Any]]] = []
    g = state.get("global")
    if isinstance(g, dict):
        candidates.append(("global", g))

    if symbol and side:
        key = f"{str(symbol).strip()}|{str(side).strip().lower()}"
        d = (state.get("by_symbol_side") or {}).get(key)
        if isinstance(d, dict):
            candidates.append((f"symbol_side:{key}", d))
        else:
            # 该 symbol+方向尚无样本 → 视为 warmup
            candidates.append((f"symbol_side:{key}", {"stage": STAGE_WARMUP, "conf_relief": _cap}))

    if nature and tier:
        key = f"{str(nature).strip().lower()}|{str(tier).strip().lower()}"
        d = (state.get("by_nature_tier") or {}).get(key)
        if isinstance(d, dict):
            candidates.append((f"nature_tier:{key}", d))
        else:
            candidates.append((f"nature_tier:{key}", {"stage": STAGE_WARMUP, "conf_relief": _cap}))

    if not candidates:
        return {"relief": 0.0, "stage": STAGE_MATURE, "driver": "none"}

    min_rank = min(_stage_rank(c[1].get("stage", STAGE_MATURE)) for c in candidates)

    if min_rank == 0:  # 有 warmup
        warmups = [c for c in candidates if _stage_rank(c[1].get("stage")) == 0]
        # 放宽取最大（鼓励累积），封顶
        best = max(warmups, key=lambda c: float(c[1].get("conf_relief", 0)))
        return {
            "relief": min(_cap, float(best[1].get("conf_relief", _cap))),
            "stage": STAGE_WARMUP,
            "driver": best[0],
        }
    if min_rank == 1:  # 有 growth、无 warmup
        growths = [c for c in candidates if _stage_rank(c[1].get("stage")) == 1]
        best = max(growths, key=lambda c: float(c[1].get("conf_relief", 0)))
        return {
            "relief": float(best[1].get("conf_relief", 0)),
            "stage": STAGE_GROWTH,
            "driver": best[0],
        }
    # 全 mature：取最保守（最小 relief，可能为负=收紧）
    worst = min(candidates, key=lambda c: float(c[1].get("conf_relief", 0)))
    return {
        "relief": float(worst[1].get("conf_relief", 0)),
        "stage": STAGE_MATURE,
        "driver": worst[0],
    }


def get_global_stage(mode: str = "paper") -> str:
    """快捷读全局阶段（live 恒为 mature）。"""
    if (mode or "paper").lower() == "live":
        return STAGE_MATURE
    state = get_maturity_state()
    g = state.get("global") if state else None
    if isinstance(g, dict):
        return str(g.get("stage") or STAGE_MATURE)
    return STAGE_WARMUP
