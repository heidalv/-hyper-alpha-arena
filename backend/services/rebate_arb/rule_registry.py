"""RuleRegistry — 六所规则源注册表。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, asdict
from typing import Any, Dict, List


@dataclass(frozen=True)
class RuleSource:
    source_id: str
    exchange: str
    rule_type: str
    title: str
    url: str
    affected_strategies: List[str]
    auto_pause_enabled: bool

    def to_dict(self) -> Dict:
        return asdict(self)


RULE_SOURCES: List[RuleSource] = [
    RuleSource("hl_points_docs", "hyperliquid", "points", "Hyperliquid Points", "https://hyperliquid.gitbook.io/hyperliquid-docs/", ["S3", "S5"], True),
    RuleSource("hl_fees_docs", "hyperliquid", "fees", "Hyperliquid Fees", "https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees", ["S5"], True),
    RuleSource("binance_alpha_rules", "binance", "alpha_points", "Binance Alpha Points", "https://www.binance.com/en/support/announcement", ["S7"], True),
    RuleSource("binance_fees", "binance", "fees", "Binance Fee Schedule", "https://www.binance.com/en/fee/trading", ["S1", "S7"], True),
    RuleSource("asterdex_rh_rules", "asterdex", "rh_points", "Asterdex Rh/ASTER Rules", "https://www.asterdex.com/", ["S8"], True),
    RuleSource("asterdex_fees", "asterdex", "fees", "Asterdex Fees", "https://www.asterdex.com/", ["S1", "S6", "S8"], True),
    RuleSource("okx_vip_fees", "okx", "vip_fees", "OKX VIP/Fee Rules", "https://www.okx.com/fees", ["S2", "S4"], False),
    RuleSource("okx_campaigns", "okx", "campaigns", "OKX Campaign Rules", "https://www.okx.com/help/section/announcements", ["S4"], False),
    RuleSource("bybit_fees", "bybit", "fees", "Bybit Fee Rules", "https://www.bybit.com/en/announcement-info/", ["S4", "S6"], False),
    RuleSource("bybit_campaigns", "bybit", "campaigns", "Bybit Campaign Rules", "https://announcements.bybit.com/", ["S4"], False),
    RuleSource("gateio_fees", "gateio", "fees", "Gate.io Fee Rules", "https://www.gate.io/fee", ["S4", "S6"], False),
    RuleSource("gateio_campaigns", "gateio", "campaigns", "Gate.io Campaign Rules", "https://www.gate.io/announcements", ["S4"], False),
]


# ══════════════════════════════════════════════════════════════════
# [2026-09-03 核实] Asterdex 当前有效激励 —— Trade & Earn（进行中）
#
# 官方文档 docs.asterdex.com/program-and-rewards/trade-and-earn：
# - 用 USDF / asBNB 作永续保证金即可获得奖励，每周以 USDF 自动发到合约账户
# - 存款奖励：账户 USDF > 1 即按小时快照发放（完全被动，不需要交易）
# - 交易奖励：每周活跃 ≥2 天 且 周成交 ≥ $50,000（2025-11-20 起），
#   按 USDF 持仓快照发放，单账户计入上限 100,000 USDF（2025-09-04 起）
# - 结算周期：周四 → 下周三；7 个工作日内发放
# - 抵押率：USDF 99.99%，asBNB 95%；需开启 Multi-Asset Mode
# - 刷量 / 操纵 / 批量开号 → 取消资格
#
# 真实费率（docs.asterdex.com/trading/perpetuals/fees-and-specs/fees）：
# - USDT 永续 Maker 0% / Taker 0.04%；USD1 永续 Maker 0% / Taker 0.005%
# - 用 $ASTER 支付手续费再省 5%
#
# 年化为「参考值」：官方按周池子动态浮动，不承诺固定利率；
# 第三方 2025-2026 快照口径约 存款 4% + 交易 5.6%。前端必须标注"参考"。
# ══════════════════════════════════════════════════════════════════
TRADE_AND_EARN_PROGRAM: Dict[str, Any] = {
    "name": "Trade & Earn",
    "active": True,
    "source_url": "https://docs.asterdex.com/program-and-rewards/trade-and-earn",
    "reward_asset": "USDF",
    # 交易奖励门槛（存款奖励无门槛）
    "weekly_volume_threshold_usd": 50_000.0,
    "weekly_active_days_threshold": 2,
    "usdf_counted_cap": 100_000.0,
    # 结算窗口：周四 00:00 UTC → 下周三 23:59 UTC（ISO 周四 = 3）
    "week_start_weekday": 3,
    # 抵押率（多资产模式下保证金折算）
    "collateral_ratio": {"USDF": 0.9999, "asBNB": 0.95},
    "eligible_collateral": ["USDF", "asBNB"],
    "requires_multi_assets_mode": True,
    # 参考年化（非官方承诺，动态浮动）
    "reference_apy": {"deposit": 0.04, "trading": 0.056, "note": "参考值，官方按周动态浮动"},
    # 费率单一来源
    "fee_schedule": {
        "usdt_perp": {"maker": 0.0, "taker": 0.0004},
        "usd1_perp": {"maker": 0.0, "taker": 0.00005},
        "aster_fee_discount": 0.05,
    },
    "wash_trade_policy": "刷量/对冲刷分/批量开号取消资格 → 只优化本来就要成交的单",
}


# ══════════════════════════════════════════════════════════════════
# Asterdex Stage 6 Convergence 积分模型 —— [2026-09-03 核实] 已结束
#
# Stage 6 空投（6400 万 ASTER）已于 2026-05 开放领取（50% 即时领取窗口
# 5/4–6/4 已关闭；100% 锁仓领取窗口 11/4–12/4）。官方文档未公布 Stage 7。
# 因此：现在为积分多交易一笔 = 纯付手续费、零回报。本模型仅作历史口径保留
# （live_points_engine / S8 / 单测仍引用其结构），"active": False 时估值归零。
#
#   总积分 = (交易积分 + 持仓积分 + Aster资产积分 + 清算积分 + 盈亏积分)
#            × 团队加成(1.05-1.2x) + 推荐积分
#
# 注意：官方从未公开各类别精确权重，以下数值为估算值（estimate）。
# ══════════════════════════════════════════════════════════════════
STAGE6_POINT_MODEL: Dict[str, Any] = {
    "version": "stage6_convergence_v1",
    # [2026-09-03] 赛季状态：已结束、无后继赛季 → 积分估值必须归零
    "active": False,
    "stage_status": "ended",
    "stage_note": "Stage 6 空投已于 2026-05 发放，官方未公布 Stage 7；积分不再产生价值",
    "formula": "(trading + position + aster_asset + liquidation + pnl) * team_boost + referral",
    # ── 费率单一来源（fee_schedule）：策略/YAML/EV 模型统一从这里读 ──
    "fee_schedule": {
        "usdt_perp": {"maker": 0.0, "taker": 0.0004},
        "usd1_perp": {"maker": 0.0, "taker": 0.00005},
        # 用 ASTER 支付手续费再省 5%
        "aster_fee_discount": 0.05,
    },
    # ── 交易积分：手续费贡献 + Maker 流动性贡献，乘以币种加成 ──
    "trading": {
        # 每 $1 手续费贡献的积分（估算可调）
        "points_per_usd_fee": 100.0,
        # Maker 挂单每 $1k 成交名义的流动性积分（0 费率仍计分，估算可调）
        "maker_points_per_1k_usd": 1.0,
    },
    # ── 持仓积分：规模 × 时长，无上限，T+1 ──
    "position": {
        "points_per_1k_usd_hour": 0.5,
    },
    # ── Aster 资产积分：USDF/ASTER/asBNB 保证金余额，需全仓模式，无上限 ──
    "aster_asset": {
        "points_per_1k_usd_hour": 1.0,
        "requires_cross_margin": True,
        "eligible_assets": ["USDF", "ASTER", "asBNB"],
    },
    # ── 盈亏积分：每小时净盈亏（不含资金费）计入；双向都算但真实亏损是真亏 ──
    "pnl": {
        "points_per_usd_abs_pnl": 0.5,
    },
    # ── 清算积分：清算费计分 → 对交易者是纯损失，策略上必须避免清算 ──
    "liquidation": {
        "enabled": False,
        "note": "清算积分是清算费的补偿，主动追求等于烧钱，杠杆控制避免清算",
    },
    # ── 团队加成 / 推荐积分（账号运营层面，代码内只做展示） ──
    "team_boost": {"min": 1.05, "max": 1.20, "default": 1.05},
    "referral": {"note": "推荐积分独立累加，不进入 EV 模型"},
    # ── 积分估值（投机性！官方未承诺兑换比例，按周空投池摊算的估值折扣后使用）──
    "point_valuation": {
        "usd_per_point_estimate": 0.01,
        "speculative_discount": 0.5,
        "note": "估值 = usd_per_point_estimate × speculative_discount，前端需标注投机性",
    },
    "wash_trade_policy": "官方明确惩罚对冲刷分，wash trade 直接取消资格 → 仅做单边方向仓",
}


STRATEGY_RULE_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "S7": {
        "MODE": "monitor_only",
        "TOKENS_PER_POINT": 22,
        "AVG_TOKEN_VALUE": 0.50,
        "GRADUATION_RATE": 0.475,
        "FIRST_DAY_PREMIUM": 3.0,
        "rule_source_ids": ["binance_alpha_rules", "binance_fees"],
        "note": "S7 remains monitor_only until Rule Sync has enough stable snapshots.",
    },
    "S8": {
        # ── Stage 6 费率校正（旧值 taker 0.005% 与官方差 8 倍，已校正）──
        "TAKER_FEE": 0.0004,    # USDT 永续 0.04% taker
        "MAKER_FEE": 0.0,       # Stage 6 Maker 0% 且赚积分
        "REBATE_RATE": 0.0,     # 旧赛季 10% 返佣假设废弃（保守按 0，邀请返佣另算）
        "MIN_HOLD_SECONDS": 3600,
        "HOLD_BUFFER_SECONDS": 300,
        "STAGE_6_ALLOCATION": 64_000_000,
        "STAGE_EPOCHS": 12,
        "ASTER_PRICE": 0.70,
        # Stage 6 积分类别模型（取代旧 80x 乘数模型）
        "STAGE6_MODEL": STAGE6_POINT_MODEL,
        # ── 旧 80x 乘数模型参数：仅兼容保留，EV 不再使用 ──
        "USDF_AU_MULTIPLIER": 20,
        "TAKER_RH_WEIGHT": 2.0,
        "HOLD_TIME_BOOST": 2.0,
        "LEGACY_MULTIPLIER_DEPRECATED": True,
        "rule_source_ids": ["asterdex_rh_rules", "asterdex_fees"],
    },
}


class RuleRegistry:
    """Static MVP registry; DB-backed source editing can be added later."""

    def __init__(self):
        self._sources = {s.source_id: s for s in RULE_SOURCES}

    def list_sources(self) -> List[Dict]:
        return [s.to_dict() for s in RULE_SOURCES]

    def get_source(self, source_id: str) -> RuleSource:
        source = self._sources.get(source_id)
        if not source:
            raise KeyError(f"unknown rule source: {source_id}")
        return source

    def get_strategy_rule_params(self, strategy_id: str) -> Dict[str, Any]:
        """Return rule-backed defaults for a strategy."""
        sid = (strategy_id or "").upper()
        params = deepcopy(STRATEGY_RULE_DEFAULTS.get(sid, {}))
        params["strategy_type"] = sid
        params["source"] = "rule_registry_defaults"
        return params

    def list_strategy_rule_params(self) -> Dict[str, Dict[str, Any]]:
        return {
            sid: self.get_strategy_rule_params(sid)
            for sid in sorted(STRATEGY_RULE_DEFAULTS)
        }


rule_registry = RuleRegistry()
