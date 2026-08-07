"""积分项目生命周期 + 费率 + 积分规则注册表（离线权威数据源）。

2026-07-06 新增（修复病灶 B/C/D）：
    本环境无法联网抓取交易所（RuleChangeDetector 全部超时/连接失败），也没有交易所
    客户端/密钥（IncentiveAggregator "No adapters available"），因此"靠实时抓取规则/
    费率/积分"这条路在当前部署下走不通。正确架构是：以本模块作为**离线、可人工维护的
    权威数据源**，实时抓取仅作"有网络时的可选刷新"。

用途：
    1. 程序生命周期：每个刷分项目带 status（active/ended/staking_only/monitor_only）
       与起止日期，让引擎/策略"只对活着的项目分配资金"——根治"主力还在刷已于
       2026-03-29 结束的 Aster Stage 6"这一核心逻辑不通。
    2. 离线费率/积分兜底：IncentiveAggregator 无 adapter 时从这里读 maker/taker 费率
       与积分规则摘要，而不是返回空并静默。
    3. 策略↔项目映射：策略初始化/扫描时可用 is_strategy_program_active() 自检。

维护方式：
    交易所出新赛季/活动结束时，直接改本文件的 _PROGRAMS 常量即可（纯数据，无副作用）。
    日期用 UTC date；status 变化以官方公告为准。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ─────────────────────── 数据结构 ───────────────────────

# 程序状态语义：
#   active        —— 正在进行、交易可累积积分/空投权重（值得刷）
#   ended         —— 活动已结束，刷该项目无意义（必须停止分配资金）
#   staking_only  —— 已从"交易刷分"转为"质押/锁仓"模式，交易不再直接产生空投积分
#   monitor_only  —— 只监控、不刷（例如规则变更导致交易量不再计分，或 API 不可用）
#   upcoming      —— 已公布但尚未开始
VALID_STATUS = ("active", "ended", "staking_only", "monitor_only", "upcoming")


@dataclass
class PointsProgram:
    """单个交易所积分/激励项目的生命周期与规则快照。"""

    program_id: str
    exchange: str
    name: str
    status: str
    # 费率（永续 USDT 本位，小数：0.0002 = 0.02%）
    maker_rate: float = 0.0002
    taker_rate: float = 0.0005
    rebate_rate: float = 0.0
    # 生命周期（UTC）；start/end 为 None 表示未知或长期
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    # 积分规则摘要（人类可读，供前端/日志展示，非机器计算）
    points_rule: str = ""
    # 该项目对应的 Rebate 策略 ID（如 "S3"）；None 表示暂无对应策略
    strategy_id: Optional[str] = None
    # 备注 / 数据来源
    notes: str = ""

    # ── 可选积分估值参数（默认全 None → 诚实地判"不可估"，积分价值按 0 计） ──
    # 只有维护者据公开信息填入真实数字后，SDN 的净 EV 才会计入折现后积分价值。
    # 绝不臆造：拿不到可靠 FDV/总积分/累积速率时保持 None，宁可低估为 0。
    expected_fdv_usd: Optional[float] = None       # 项目预期完全稀释估值(USD)
    airdrop_supply_pct: float = 0.10               # 空投给积分持有者的供应占比(0~1)
    total_points_estimate: Optional[float] = None  # 全网预计总积分
    # 每 $1000 单腿名义、每天预计累积的积分数（用于把名义×天数换算成"我的积分"）
    points_per_1k_usd_per_day: Optional[float] = None

    def is_active(self, on: Optional[date] = None) -> bool:
        """给定日期该项目是否处于可刷（active）状态。"""
        if self.status != "active":
            return False
        d = on or _today()
        if self.start_date and d < self.start_date:
            return False
        if self.end_date and d > self.end_date:
            return False
        return True

    def to_dict(self) -> Dict:
        return {
            "program_id": self.program_id,
            "exchange": self.exchange,
            "name": self.name,
            "status": self.status,
            "maker_rate": self.maker_rate,
            "taker_rate": self.taker_rate,
            "rebate_rate": self.rebate_rate,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "points_rule": self.points_rule,
            "strategy_id": self.strategy_id,
            "notes": self.notes,
            "is_active_now": self.is_active(),
            "expected_fdv_usd": self.expected_fdv_usd,
            "airdrop_supply_pct": self.airdrop_supply_pct,
            "total_points_estimate": self.total_points_estimate,
            "points_per_1k_usd_per_day": self.points_per_1k_usd_per_day,
            "points_valuation_ready": self.points_valuation_ready(),
        }

    def points_valuation_ready(self) -> bool:
        """是否已填齐做诚实积分估值所需的全部参数（否则积分价值按 0 计）。"""
        return (
            self.expected_fdv_usd is not None
            and self.expected_fdv_usd > 0
            and self.total_points_estimate is not None
            and self.total_points_estimate > 0
            and self.points_per_1k_usd_per_day is not None
            and self.points_per_1k_usd_per_day > 0
        )


def _today() -> date:
    return datetime.now(timezone.utc).date()


# ─────────────────── 权威数据（2026-07-06 校准） ───────────────────
# 依据：交易所官方文档 + 2026-07 公开动态检索。
_PROGRAMS: Dict[str, PointsProgram] = {
    # Hyperliquid Season 2：进行中，recency-weighted，多产品（perp/spot/HLP/质押）。
    "hyperliquid_season2": PointsProgram(
        program_id="hyperliquid_season2",
        exchange="hyperliquid",
        name="Hyperliquid Points Season 2",
        status="active",
        maker_rate=0.00015,
        taker_rate=0.00045,
        start_date=date(2026, 1, 1),
        end_date=None,  # 官方未公布快照日；社区判断至少开放到 2026 年中后
        points_rule="按成交量/做市/多产品(perp+spot+HLP+质押)累积，近期活动权重更高(recency-weighted)",
        strategy_id="S3",
        notes="2026-07 仍在进行；快照日未公布，保持持续、多产品活跃优于突击刷量。",
    ),
    # Aster Stage 6 Convergence：已于 2026-03-29 结束，转 veASTER 质押/回购模式。
    "aster_stage6": PointsProgram(
        program_id="aster_stage6",
        exchange="asterdex",
        name="Aster Convergence Stage 6 (Rh Points)",
        status="ended",
        maker_rate=0.0,
        taker_rate=0.0004,
        start_date=date(2026, 2, 2),
        end_date=date(2026, 3, 29),
        points_rule="[已结束] Rh积分=交易+持仓+资产(USDF)+清算+盈亏 x team boost + 推荐",
        strategy_id="S8",
        notes="2026-03-29 结束刷分，改为 veASTER 质押(150k Base + 300k Loyalty/epoch)+回购销毁；交易刷分不再产生空投。",
    ),
    # Aster 质押模型（Stage 6 之后的现实）：交易不直接产生空投积分，只对质押者有交易量 boost。
    "aster_staking": PointsProgram(
        program_id="aster_staking",
        exchange="asterdex",
        name="Aster veASTER Dual-Reward Staking",
        status="staking_only",
        maker_rate=0.0,
        taker_rate=0.0004,
        start_date=date(2026, 3, 30),
        end_date=None,
        points_rule="质押 veASTER 得 Base+Loyalty APY；交易量仅作质押者奖励 boost(>500K=1.05x…)",
        strategy_id=None,
        notes="需锁仓 ASTER 才有意义，非'纯交易刷分'；当前小资金 Paper 方案不主推。",
    ),
    # Binance Alpha：2025-06-17 后交易量不再计积分，API 不可用 → 只监控。
    "binance_alpha": PointsProgram(
        program_id="binance_alpha",
        exchange="binance",
        name="Binance Alpha Points",
        status="monitor_only",
        maker_rate=0.0002,
        taker_rate=0.0004,
        start_date=None,
        end_date=None,
        points_rule="[仅监控] 2025-06-17 后交易量不再计积分；规则/额度以官方为准",
        strategy_id="S7",
        notes="不作可执行刷分策略；Binance 主要作为 delta-neutral 的深流动性对冲腿。",
    ),
    # 新一批仍在进行的积分 DEX（2026-07 活跃），用于 delta-neutral 刷分的"多腿"候选。
    "backpack_season4": PointsProgram(
        program_id="backpack_season4",
        exchange="backpack",
        name="Backpack Season 4 Points",
        status="active",
        maker_rate=0.0,
        taker_rate=0.0004,
        start_date=date(2025, 11, 20),
        end_date=None,
        points_rule="交易量为主(现货/合约)+钱包DeFi+Pay+推荐；社区分配25%供应量",
        strategy_id=None,
        notes="2026-02-09 公布 TGE 计划(社区25%)；Season4 进行中。",
    ),
    "paradex_season2": PointsProgram(
        program_id="paradex_season2",
        exchange="paradex",
        name="Paradex Season 2 XP",
        status="active",
        maker_rate=0.0,
        taker_rate=0.0003,
        start_date=date(2025, 1, 3),
        end_date=None,
        points_rule="每周五发放 4M XP 给活跃交易者；含做市返佣",
        strategy_id=None,
        notes="StarkNet L2；零费/低费，适合做多腿。",
    ),
    "lighter_points": PointsProgram(
        program_id="lighter_points",
        exchange="lighter",
        name="Lighter Points (Standard Account)",
        status="active",
        maker_rate=0.0,
        taker_rate=0.0002,
        start_date=None,
        end_date=None,
        points_rule="成交量+做市均计分；Standard 账户 0bps maker",
        strategy_id=None,
        notes="0 maker 费 → 即使资金费价差很窄也能覆盖成本，是优质多腿候选。",
    ),
    "pacifica_points": PointsProgram(
        program_id="pacifica_points",
        exchange="pacifica",
        name="Pacifica Pre-TGE Points",
        status="active",
        maker_rate=0.0,
        taker_rate=0.0004,
        start_date=None,
        end_date=None,
        points_rule="Pre-TGE 交易积分；小时级资金费常与 CEX 背离",
        strategy_id=None,
        notes="Solana 上较干净的多腿候选；资金费波动大。",
    ),
    "extended_points": PointsProgram(
        program_id="extended_points",
        exchange="extended",
        name="Extended Points",
        status="active",
        maker_rate=0.0,
        taker_rate=0.0004,
        start_date=date(2025, 4, 30),
        end_date=None,
        points_rule="每周 1.2M 积分分给交易者与 LP",
        strategy_id=None,
        notes="StarkNet；CLOB 混合模型。",
    ),
}

# 交易所 → 默认离线费率（用于 IncentiveAggregator 无 adapter 时兜底；非积分项目的普通场所）
_EXCHANGE_DEFAULT_FEES: Dict[str, Dict[str, float]] = {
    "binance": {"maker_rate": 0.0002, "taker_rate": 0.0004, "rebate_rate": 0.0},
    "okx": {"maker_rate": 0.0002, "taker_rate": 0.0005, "rebate_rate": 0.0},
    "bybit": {"maker_rate": 0.0002, "taker_rate": 0.00055, "rebate_rate": 0.0},
    "gateio": {"maker_rate": 0.0002, "taker_rate": 0.0005, "rebate_rate": 0.0},
    "hyperliquid": {"maker_rate": 0.00015, "taker_rate": 0.00045, "rebate_rate": 0.0},
    "asterdex": {"maker_rate": 0.0, "taker_rate": 0.0004, "rebate_rate": 0.0},
}


# ─────────────────────── 查询 API ───────────────────────

def get_program(program_id: str) -> Optional[PointsProgram]:
    return _PROGRAMS.get(program_id)


def all_programs() -> List[PointsProgram]:
    return list(_PROGRAMS.values())


def active_programs(on: Optional[date] = None) -> List[PointsProgram]:
    """当前处于可刷(active)状态的项目列表。"""
    return [p for p in _PROGRAMS.values() if p.is_active(on)]


def get_program_for_strategy(strategy_id: str) -> Optional[PointsProgram]:
    """按策略 ID 找到其对应的积分项目（strategy_id 匹配，取第一个）。"""
    sid = (strategy_id or "").upper()
    if not sid:
        return None
    for p in _PROGRAMS.values():
        if (p.strategy_id or "").upper() == sid:
            return p
    return None


def is_strategy_program_active(strategy_id: str) -> bool:
    """该策略对应的积分项目是否仍值得刷。

    - 没有对应积分项目的策略（如纯资金费套利 S6、VIP 冲刺 S2）→ 视为 True（不受
      项目生命周期约束，由策略自身 EV 决定）。
    - 有对应项目但项目已 ended/staking_only/monitor_only → False（应跳过、不占配额）。
    """
    prog = get_program_for_strategy(strategy_id)
    if prog is None:
        return True
    return prog.is_active()


def strategy_program_status(strategy_id: str) -> str:
    """返回策略对应项目的状态字符串；无对应项目返回 'no_program'。"""
    prog = get_program_for_strategy(strategy_id)
    return prog.status if prog else "no_program"


def get_offline_incentive(exchange: str) -> Dict[str, float]:
    """交易所离线费率兜底（IncentiveAggregator 无 adapter 时用）。

    优先用该所 active 积分项目的费率；否则用默认费率表；再否则给通用兜底值。
    """
    ex = (exchange or "").lower()
    for p in _PROGRAMS.values():
        if p.exchange == ex and p.is_active():
            return {
                "maker_rate": p.maker_rate,
                "taker_rate": p.taker_rate,
                "rebate_rate": p.rebate_rate,
            }
    if ex in _EXCHANGE_DEFAULT_FEES:
        return dict(_EXCHANGE_DEFAULT_FEES[ex])
    return {"maker_rate": 0.0002, "taker_rate": 0.0005, "rebate_rate": 0.0}


def estimate_my_points(
    program_id: str, notional_usd: float, horizon_days: float
) -> Optional[float]:
    """按名义×天数×项目累积速率估算"我方观察期能拿到的积分数"。

    项目未填 points_per_1k_usd_per_day 时返回 None（→ 上游按不可估、积分价值 0 处理）。
    """
    prog = _PROGRAMS.get(program_id)
    if prog is None or prog.points_per_1k_usd_per_day is None:
        return None
    if notional_usd <= 0 or horizon_days <= 0:
        return 0.0
    return (notional_usd / 1000.0) * prog.points_per_1k_usd_per_day * horizon_days


def get_points_valuation_params(program_id: str) -> Optional[Dict]:
    """取项目积分估值参数（FDV/空投占比/总积分）；未填齐返回 None。"""
    prog = _PROGRAMS.get(program_id)
    if prog is None or not prog.points_valuation_ready():
        return None
    return {
        "expected_fdv_usd": prog.expected_fdv_usd,
        "airdrop_supply_pct": prog.airdrop_supply_pct,
        "total_points_estimate": prog.total_points_estimate,
    }


def summary() -> Dict[str, Dict]:
    """全项目状态摘要（供前端/诊断展示）。"""
    return {pid: p.to_dict() for pid, p in _PROGRAMS.items()}
