"""BudgetService — Layer/Tier 预算的**唯一事实来源**（Single Source of Truth）。

2026-07-06 整改（P2 BudgetService 统一）：此前存在"双账本"——
`layer_budget_manager.LayerBudgetManager` 与本类各自持有一份层分配比例，且本类
反过来依赖旧类（get_used_margin / tier_to_layer fallback 调它，甚至调其私有方法
`_get_layer_used_margin`），等于"新壳套老核"，并未真正替代。本次把旧类的核心逻辑
（nature→layer 映射、层已用保证金 DB 聚合查询）全部收编进本类，旧模块随之删除，
预算相关配置与查询从此只有这一处定义，消除双写与潜在不一致。
"""

from __future__ import annotations

import logging
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# tier / nature → 两层（scalp/trend）的唯一映射。
# 中长线合并收尾（2026-07）：mid/swing 已并入 long/trend，不再是独立层。
# swing 仅作为向后兼容的"重定向"入口——历史 DB 仓位 trade_nature='swing' 仍会
# 经 nature_to_layer 映射到 trend，从 trend 池分配预算，不会报错也不会落空。
# tier 与 nature 语义在此统一收口，其它模块不再各自维护映射表。
TIER_TO_LAYER: Dict[str, str] = {
    "short": "scalp",
    "mid": "trend",      # 中长线合并：mid → trend（原 swing 层已并入 trend）
    "long": "trend",
    "scalp": "scalp",
    "swing": "trend",    # 向后兼容：旧调用传 swing → 走 trend 池
    "trend": "trend",
}

NATURE_TO_LAYER: Dict[str, Optional[str]] = {
    # [轮116 2026-09-19] `scalp` / `intraday` 从 scalp 层改到 **trend** 层。
    # 依据是轮63（2026-09-18）用户确认的车道口径：交易只有两条车道 ——
    #   车道1 `intraday` 日内（含中线槽位，tier=mid） / 车道2 `trend` 长线趋势（tier=long），
    #   且"`scalp` 不再是独立车道：短线车道已停（2026-09-17），存量 scalp 仓位归入 intraday"。
    # 旧映射把 scalp/intraday 的保证金记进**已停开的 scalp 池**（旧配额 0.35），
    # 于是日内车道的钱从一个死池子里出 —— 预算层还停在"三车道"时代。
    "scalp": "trend", "intraday": "trend",
    "swing": "trend",    # 中长线合并：swing 重定向到 trend（不报错）
    "trend_follow": "trend", "position": "trend",
}

# ── [轮116 2026-09-19] tier 别名 → 预算 tier（**唯一**映射处）──────────────
# 三车道时代（short/mid/long）在预算层留下的三份配额，与轮63 确认的**两车道**
# （intraday/trend）不一致：`short` 那条车道已经停了（SCALP_OPEN_DISABLED=true，
# lane_registry 标 "永久关闭"），配额却还是 0.25，从来没被回收/重分配。
#
# 本表把一切写法收敛到三个**预算桶**（不是报告车道）：
#   short = 已停的存量短线（配额 0，只留给仍在跑的旧路径做显式拦截）
#   mid   = 日内（轮63：`LANE_SPECS['intraday'].tier == 'mid'`，含 swing/中线槽位）
#   long  = 长线趋势
# 未知标签一律 **None**（fail-closed），不再静默兜底成 mid ——
# 原 `get_tier_cap` 用 `else: tier_l = "mid"` 兜底：'1h' / '15m' / 'scalp_directional'
# 这类标签会**拿到 mid 的配额**，而 `tier_to_layer()` 对它们返回 None
# ⇒ 持仓既不占任何层额度、又有一个看起来存在的 tier 上限（没人管得着的幽灵配额）。
TIER_ALIASES: Dict[str, str] = {
    "short": "short", "scalp": "short", "shortterm": "short", "short_term": "short",
    "mid": "mid", "swing": "mid", "intraday": "mid", "midlong": "mid",
    "midterm": "mid", "mid_term": "mid", "medium": "mid", "daytrade": "mid",
    "day_trade": "mid",
    "long": "long", "trend": "long", "trend_follow": "long", "trendfollow": "long",
    "position": "long", "longterm": "long", "long_term": "long",
}

_TIER_UNKNOWN_WARNED: set = set()

# 研究车道：**已知但刻意不做预算**（不属于任何交易层）。
# 与"未知标签"必须区分：前者是"不在预算体系内"（不缩仓、不拦），
# 后者是"没人认识的标签"（fail-closed）。原实现把两者混为一谈
# （未知一律 1.0 = 不缩仓），于是 '1h' / '15m' 这类脏标签可以完全绕过预算。
RESEARCH_TIERS: frozenset = frozenset({"research", "pair_research", "arb", "arbitrage"})


def is_research_tier(tier: str) -> bool:
    return str(tier or "").strip().lower().replace("-", "_") in RESEARCH_TIERS


def normalize_tier(tier: str) -> Optional[str]:
    """任意 tier/nature 写法 → 预算桶 short|mid|long；未知返回 None（调用方显式处理）。

    `research` / `pair_research` 等研究车道**刻意**不映射（返回 None）：它们不属于
    任何交易预算层，`tier_to_layer()` 也返回 None。
    """
    t = str(tier or "").strip().lower().replace("-", "_").replace(" ", "")
    if not t:
        # 未指定 ≠ 非法：沿用全库既有约定（`tier_to_layer` / `sub_position_manager`
        # 都用 `or "mid"`）—— 空值按**默认车道**（日内/中线槽位）处理。
        return "mid"
    if t in ("research", "pair_research", "arb", "arbitrage"):
        return None
    r = TIER_ALIASES.get(t)
    if r is None and t not in _TIER_UNKNOWN_WARNED:
        _TIER_UNKNOWN_WARNED.add(t)
        logger.warning(
            "[BudgetService] 未知 tier=%r：无法归入 short/mid/long，按 **0 配额** 处理"
            "（fail-closed）。若这是新车道，请登记到 TIER_ALIASES。", t)
    return r


def tier_for_position(pos: Any) -> Optional[str]:
    """一条持仓 → 预算桶。**以 `lane_semantics` 为车道真源**（轮63），不推导存储 tier。

    `sub_position_manager.NATURE_TO_TIER` 是**引擎存储档位**（intraday 存成 short），
    与预算/报告车道刻意不同（该模块 :120-124 已写明"两者不要互相推导"）。
    预算按**钱**归属，必须跟车道走：scalp/intraday/swing → 日内(mid)，trend_follow → 趋势(long)。
    """
    try:
        from backend.config.lane_semantics import (
            LANE_INTRADAY, LANE_TREND, resolve_lane_for_position,
        )
        lane = resolve_lane_for_position(pos)
        if lane == LANE_TREND:
            return "long"
        if lane == LANE_INTRADAY:
            return "mid"
        return None
    except Exception:
        return None


class BudgetService:
    """两层预算(scalp/trend) + tier/nature 映射，预算体系的单一事实来源。"""

    _instance: Optional["BudgetService"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def layer_allocations(self) -> Dict[str, float]:
        """两层资金分配比例（实时读 env，唯一定义处）。

        中长线合并收尾（2026-07）：原 swing 层（mid）已并入 trend（long），预算由三层
        收敛为两层 scalp / trend。原 swing 默认 0.45 直接并入 trend：
          - 激活态：scalp 0.25 / trend 0.75（原 scalp 0.25 / swing 0.45 / trend 0.30）
          - 未激活：scalp 0.40 / trend 0.60（原 scalp 0.40 / swing 0.45 / trend 0.15）
        向后兼容：若仍显式设置了 `LAYER_BUDGET_SWING` env，其值会叠加进 trend，避免旧部署
        的 env 配置静默失效；历史 DB 仓位 trade_nature='swing' 经 nature_to_layer 重定向
        到 trend，从 trend 池分配预算。
        """
        try:
            from backend.config.settings import MIDLONG_ACTIVATION_ENABLED
            _act = bool(MIDLONG_ACTIVATION_ENABLED)
        except Exception:
            _act = False
        _def_scalp = "0.25" if _act else "0.40"
        _def_trend = "0.75" if _act else "0.60"
        _scalp = float(os.getenv("LAYER_BUDGET_SCALP", _def_scalp))
        _trend = float(os.getenv("LAYER_BUDGET_TREND", _def_trend))
        # 兼容旧 env：LAYER_BUDGET_SWING 叠加进 trend（不再作为独立层）
        _swing_legacy = os.getenv("LAYER_BUDGET_SWING")
        if _swing_legacy not in (None, ""):
            try:
                _trend += float(_swing_legacy)
            except (TypeError, ValueError):
                pass
        return {
            "scalp": _scalp,
            "trend": _trend,
        }

    # ── 映射 ──────────────────────────────────────────────
    def nature_to_layer(self, nature: str) -> Optional[str]:
        """trade_nature → layer（scalp/trend），未知返回 None。

        中长线合并后 swing 不再是独立层；未知 nature（pair_research/research 等研究
        车道）不属于任何交易预算层，返回 None，由 _query_layer_used_margin 跳过，
        不再把研究仓位误计入 trend/scalp 池。
        """
        return NATURE_TO_LAYER.get((nature or "").lower())

    def tier_to_layer(self, tier: str) -> Optional[str]:
        """tier/nature → layer；先查 tier 表，再退到 nature 映射。"""
        t = (tier or "mid").lower()
        if t in TIER_TO_LAYER:
            return TIER_TO_LAYER[t]
        return self.nature_to_layer(t)

    # ── 已用保证金（DB 聚合，唯一实现处）──────────────────
    def _query_layer_used_margin(
        self, layer: str, account_id: Optional[int] = None, mode: str = "paper"
    ) -> float:
        """查询该层当前已用保证金：聚合 open 仓位、按 trade_nature 归到 layer。

        [S3 2026-08-21] 按 mode 分源：paper → PaperPosition；live → LiveSubPosition
        （live 子仓账本由 LivePositionManager/live_executor 维护，架构上与
        paper 账本分离）。此前 mode 形参被完全忽略、恒读 PaperPosition——
        live 模式的预算闸实际由 paper 持仓决定。

        account_id 提供时只统计该账户（会话资金池）的仓位；None 时保持全局聚合
        （供监控接口使用）。未知 nature 的仓位不计入任何交易层。
        """
        _is_live = str(mode or "paper").strip().lower() == "live"
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import LiveSubPosition, PaperPosition
            _model = LiveSubPosition if _is_live else PaperPosition
            _db = SessionLocal()
            try:
                positions = _db.query(_model).filter(
                    _model.status == "open"
                ).all()
                total = 0.0
                for p in positions:
                    if account_id is not None and int(
                        getattr(p, "account_id", 0) or 0
                    ) != int(account_id):
                        continue
                    if self.nature_to_layer((p.trade_nature or "").lower()) == layer:
                        total += float(p.margin or 0)
                return total
            finally:
                _db.close()
        except Exception as _bm_err:
            logger.warning(
                "[BudgetService] %s/%s used_margin 查询失败(按 0 计): %s",
                layer, mode, _bm_err,
            )
            return 0.0

    def get_used_margin(
        self,
        layer: str,
        mode: str = "paper",
        account_id: Optional[int] = None,
    ) -> float:
        return self._query_layer_used_margin(layer, account_id=account_id, mode=mode)

    # ── 额度 / 预算 ────────────────────────────────────────
    def get_layer_cap(self, layer: str, total_equity: float) -> float:
        # 中长线合并：未知/空 layer 兜底 trend（原 swing 兜底已并入 trend）
        alloc = self.layer_allocations.get((layer or "trend").lower(), 0.75)
        return max(0.0, float(total_equity or 0) * alloc)

    def get_tier_cap(self, tier: str, total_equity: float) -> float:
        """tier 级可用额度 = equity × min(预算占比, 单 tier 保证金上限)。

        [轮116 2026-09-19] 本方法是**唯一实现**。此前同一算式在四处各自重算：
          * 本文件（`get_tier_cap`）—— **零调用者**（死代码，阀口从来没接线）；
          * `full_auto/master_execution.py`（_tier_budget_caps，**真的在拦单**）；
          * `tier_parallel_executor.py`（tier_budgets，喂给聚合敞口上限）；
          * `api/full_auto_routes.py`（展示用）。
        与类文档宣称的"单一事实来源"相矛盾，也正是"短线配额从来没人动过"的土壤：
        改 .env 只影响其中两处，另外两处各自为政。

        未知 tier → **0.0**（fail-closed，见 `normalize_tier`），不再静默按 mid 给额度。
        """
        tier_n = normalize_tier(tier)
        if tier_n is None:
            return 0.0
        try:
            from backend.config.settings import TIER_BUDGET_ALLOCATION, TIER_MAX_MARGIN_PCT
            alloc = float(TIER_BUDGET_ALLOCATION.get(tier_n) or 0.0)
            max_pct = float(TIER_MAX_MARGIN_PCT.get(tier_n) or 0.0)
            return max(0.0, float(total_equity or 0) * min(alloc, max_pct))
        except Exception as _tc_err:
            logger.warning("[BudgetService] tier 配额读取失败(按 0 = 拦截): %s", _tc_err)
            return 0.0

    def get_tier_max_margin(self, tier: str, total_equity: float) -> float:
        """该 tier 的单仓/合计保证金上限（`TIER_MAX_MARGIN_PCT`，展示与风控共用）。"""
        tier_n = normalize_tier(tier)
        if tier_n is None:
            return 0.0
        try:
            from backend.config.settings import TIER_MAX_MARGIN_PCT
            return max(0.0, float(total_equity or 0) * float(
                TIER_MAX_MARGIN_PCT.get(tier_n) or 0.0))
        except Exception:
            return 0.0

    def get_tier_used_margin(
        self, tier: str, mode: str = "paper", account_id: Optional[int] = None,
    ) -> float:
        """该预算桶当前已用保证金（与层聚合同一份持仓来源、同一套车道归类）。"""
        tier_n = normalize_tier(tier)
        if tier_n is None:
            return 0.0
        _is_live = str(mode or "paper").strip().lower() == "live"
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import LiveSubPosition, PaperPosition
            _model = LiveSubPosition if _is_live else PaperPosition
            _db = SessionLocal()
            try:
                total = 0.0
                for p in _db.query(_model).filter(_model.status == "open").all():
                    if account_id is not None and int(
                        getattr(p, "account_id", 0) or 0
                    ) != int(account_id):
                        continue
                    if tier_for_position(p) == tier_n:
                        total += float(getattr(p, "margin", 0) or 0)
                return total
            finally:
                _db.close()
        except Exception as _tu_err:
            logger.warning("[BudgetService] %s/%s tier used_margin 查询失败(按 0 计): %s",
                           tier_n, mode, _tu_err)
            return 0.0

    def get_layer_budget(
        self,
        layer: str,
        total_equity: float,
        mode: str = "paper",
        account_id: Optional[int] = None,
        tier: Optional[str] = None,
    ) -> float:
        """该层剩余可用额度；给 `tier` 时**层与 tier 取更严**（[轮116] 接上 tier 阀口）。

        此前只算层维度 ⇒ `TIER_BUDGET_ALLOCATION` 声明的三车道配额对 mid/long 独立
        开仓路径**完全不生效**（那条路径只走 `scale_factor_for_layer`，看的是层）。
        """
        allocated = self.get_layer_cap(layer, total_equity)
        # [2026-08-28 实盘收紧] 实盘层预算 × LIVE_BUDGET_MULT（默认 0.6），
        # 模拟盘保持原口径（验证/攒样本可以粗放，实盘资金分配收紧）。
        if (mode or "paper").strip().lower() == "live":
            try:
                from backend.services.full_auto.live_gate_policy import live_budget_mult
                allocated *= float(live_budget_mult())
            except Exception:
                pass
        used = self.get_used_margin(layer, mode, account_id=account_id)
        avail = max(0.0, allocated - used)
        if tier is not None:
            _tier_n = normalize_tier(tier)
            if _tier_n is None:
                return 0.0
            _tier_avail = max(
                0.0,
                self.get_tier_cap(_tier_n, total_equity)
                - self.get_tier_used_margin(_tier_n, mode, account_id=account_id),
            )
            avail = min(avail, _tier_avail)
        return avail

    def can_open(
        self,
        tier: str,
        required_margin: float,
        total_equity: float,
        mode: str = "paper",
        account_id: Optional[int] = None,
    ) -> bool:
        layer = self.tier_to_layer(tier)
        if layer is None:
            return False
        budget = self.get_layer_budget(layer, total_equity, mode,
                                       account_id=account_id, tier=tier)
        ok = budget >= float(required_margin or 0)
        if not ok:
            logger.info(
                "[BudgetService] %s/%s 预算不足: need=%.0f avail=%.0f equity=%.0f",
                tier, layer, required_margin, budget, total_equity,
            )
        return ok

    def get_budget_utilization(
        self,
        total_equity: float,
        mode: str = "paper",
        account_id: Optional[int] = None,
    ) -> Dict[str, Dict]:
        """各层预算利用率快照（阶段二 B1 可观测性）。

        返回每层的 {alloc, cap, used, utilization, idle_pct}，用于中长线健康视图判断
        "预算是否闲置"。中长线合并后只剩 scalp/trend 两层；若 trend 层利用率长期接近 0，
        说明开仓侧仍被门槛/信号卡住，而非预算不足。
        """
        out: Dict[str, Dict] = {}
        allocs = self.layer_allocations
        eq = float(total_equity or 0)
        for layer in ("scalp", "trend"):
            cap = self.get_layer_cap(layer, eq)
            used = self.get_used_margin(layer, mode, account_id=account_id)
            util = round(used / cap, 4) if cap > 0 else 0.0
            out[layer] = {
                "alloc": round(allocs.get(layer, 0.0), 4),
                "cap": round(cap, 2),
                "used": round(used, 2),
                "utilization": util,
                "idle_pct": round(max(0.0, 1.0 - util), 4),
            }
        return out

    def get_tier_utilization(self, total_equity: float, mode: str = "paper",
                             account_id: Optional[int] = None) -> Dict[str, Dict]:
        """各预算桶（short/mid/long）的配额与用量快照 —— "阀口"的可观测面。

        [轮116] 与 `get_budget_utilization`（层维度）并列；刻意**不改**后者的返回结构，
        避免既有消费方把 `tiers` 当成第三个 layer 迭代。
        """
        out: Dict[str, Dict] = {}
        eq = float(total_equity or 0)
        try:
            from backend.config.settings import TIER_BUDGET_ALLOCATION, TIER_MAX_MARGIN_PCT
        except Exception:
            TIER_BUDGET_ALLOCATION, TIER_MAX_MARGIN_PCT = {}, {}
        for _t in ("short", "mid", "long"):
            cap = self.get_tier_cap(_t, eq)
            used = self.get_tier_used_margin(_t, mode, account_id=account_id)
            out[_t] = {
                "alloc": float(TIER_BUDGET_ALLOCATION.get(_t) or 0.0),
                "max_margin_pct": float(TIER_MAX_MARGIN_PCT.get(_t) or 0.0),
                "cap": round(cap, 2),
                "used": round(used, 2),
                "utilization": round(used / cap, 4) if cap > 0 else 0.0,
                "retired": cap <= 0,
            }
        return out

    def scale_factor_for_layer(
        self,
        tier: str,
        total_equity: float,
        mode: str = "paper",
        account_id: Optional[int] = None,
    ) -> float:
        """层/tier 预算使用 >90% 时缩仓（返回乘子）；≥100% → 0.0（调用方硬拒）。

        [轮116 2026-09-19] 三处修正：
          1. **层与 tier 双维度取更严**：tier 配额此前只是声明 —— 独立开仓路径
             （proposal_execution）只看层，`get_tier_cap` 更是零调用者；
          2. tier 无法解析 → 0.0（fail-closed，见 `normalize_tier`）；**但研究车道
             （`is_research_tier`）返回 1.0** —— 它不属于任何交易预算层，
             原语义（`tier_to_layer is None → 1.0`）对它是**正确**的，本轮只把
             "未知脏标签"和"刻意不预算"分开；
          3. **显式 0 配额 ⇒ 0.0**：原实现 `if cap <= 0: return 1.0` 让"配额=0"
             等价于"完全不限仓"，与"该车道已停"的意图正好相反。
             `equity<=0`（算不出额度）仍返回 1.0，不制造新的硬拦。
        """
        eq = float(total_equity or 0)
        if eq <= 0:
            return 1.0
        if is_research_tier(tier):
            return 1.0
        tier_n = normalize_tier(tier)
        if tier_n is None:
            return 0.0
        layer = self.tier_to_layer(tier_n)
        if layer is None:
            return 0.0
        layer_cap = self.get_layer_cap(layer, eq)
        tier_cap = self.get_tier_cap(tier_n, eq)
        if layer_cap <= 0 or tier_cap <= 0:
            return 0.0
        usage = max(
            self.get_used_margin(layer, mode, account_id=account_id) / layer_cap,
            self.get_tier_used_margin(tier_n, mode, account_id=account_id) / tier_cap,
        )
        if usage >= 1.0:
            return 0.0
        if usage >= 0.9:
            return 0.7
        return 1.0


budget_service = BudgetService()


def nature_to_layer(nature: str) -> Optional[str]:
    """模块级便捷封装：trade_nature → layer（委托单例）。

    中长线合并后 swing→trend（重定向，不报错）；未知 nature（pair_research/research
    等研究车道）返回 None，不计入任何交易预算层。
    """
    return budget_service.nature_to_layer(nature)
