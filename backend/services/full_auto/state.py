"""
FullAuto 显式状态对象（整改#8 追加件）。

背景：`full_auto_trading_service.py`（约 2 万行）把冻结冷却、日亏追踪、恢复期缩仓等状态
散落为类属性，难以测试、难以事件溯源。铁律 G3 规定「无测试网不拆 2 万行巨兽」——因此本轮
**不破坏性拆分 loop**，只先把这些边界清晰的状态抽成一个显式、可测、可序列化的 `@dataclass`，
作为 opt-in 的干净载体（衔接整改#9 事件溯源）。monolith 可在建好特征化测试后再逐步改用它。

零风险：纯数据 + 纯函数，无副作用；不 import monolith，不改动任何现有逻辑。
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Dict, Optional


@dataclass
class FullAutoState:
    """全自动交易的显式运行时状态（可测/可序列化/可事件溯源）。"""

    # ── 日内风控 ──
    daily_realized_pnl: float = 0.0            # 当日已实现盈亏
    daily_loss_limit: float = 0.0              # 当日亏损上限（<=0 表示不限）
    consecutive_losses: int = 0                # 连续亏损次数
    trading_day: str = ""                      # 当前交易日（YYYY-MM-DD，用于跨日重置）

    # ── 冻结 / 冷却 ──
    frozen: bool = False                       # 是否处于冻结（暂停开仓）
    frozen_until_ts: float = 0.0               # 冻结解除时间戳
    cooldown_by_symbol: Dict[str, float] = field(default_factory=dict)   # symbol → 冷却到期 ts

    # ── 回撤恢复期缩仓 ──
    peak_equity: float = 0.0                   # 历史峰值权益
    recovery_scale: float = 1.0                # 恢复期仓位缩放系数（0~1）

    # ── 后台循环标志 ──
    bg_scan_running: bool = False
    last_scan_ts: float = 0.0

    # ── 跨日重置 ──
    def roll_day(self, today: str) -> bool:
        """跨日时重置日内计数。返回是否发生了重置。"""
        if today != self.trading_day:
            self.trading_day = today
            self.daily_realized_pnl = 0.0
            self.consecutive_losses = 0
            return True
        return False

    # ── 冻结判定 ──
    def is_frozen(self, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        if self.frozen and now < self.frozen_until_ts:
            return True
        if self.frozen and now >= self.frozen_until_ts:
            self.frozen = False   # 到期自动解冻
        return False

    def freeze_for(self, seconds: float, now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        self.frozen = True
        self.frozen_until_ts = now + max(0.0, seconds)

    # ── 冷却判定 ──
    def in_cooldown(self, symbol: str, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        until = self.cooldown_by_symbol.get(symbol, 0.0)
        if until <= 0.0:
            return False
        if now >= until:
            self.cooldown_by_symbol.pop(symbol, None)
            return False
        return True

    def set_cooldown(self, symbol: str, seconds: float, now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        self.cooldown_by_symbol[symbol] = now + max(0.0, seconds)

    # ── 日亏 / 连亏 ──
    def register_trade_result(self, realized_pnl: float) -> None:
        """登记一笔平仓结果，更新日亏与连亏计数。"""
        self.daily_realized_pnl += float(realized_pnl)
        if realized_pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

    def daily_loss_breached(self) -> bool:
        """当日亏损是否超过上限。"""
        if self.daily_loss_limit <= 0:
            return False
        return self.daily_realized_pnl <= -abs(self.daily_loss_limit)

    # ── 回撤恢复缩仓 ──
    def update_equity(self, equity: float, recovery_position_scale: float = 0.5,
                      drawdown_trigger: float = 0.1) -> None:
        """更新峰值权益并按回撤决定恢复期缩仓系数。"""
        if equity > self.peak_equity:
            self.peak_equity = equity
        if self.peak_equity > 0:
            dd = (self.peak_equity - equity) / self.peak_equity
            self.recovery_scale = recovery_position_scale if dd >= drawdown_trigger else 1.0

    # ── 序列化（衔接事件溯源）──
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FullAutoState":
        known = {f: d[f] for f in cls.__dataclass_fields__ if f in d}
        return cls(**known)
