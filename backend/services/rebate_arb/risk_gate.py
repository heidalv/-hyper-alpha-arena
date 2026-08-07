"""
RebateRiskGate — 返利套利专项风控 (10条规则 + 熔断器)

| 规则 | 阈值 | 动作 | 冷却 |
|------|------|------|------|
| R1: 日最大刷量 | ≤$10,000/天/所 | 超额→暂停 | 24h |
| R2: 周最大刷量 | ≤$50,000/周/所 | 超额→暂停 | 7天 |
| R3: 活跃天数 | ≥2天/周 | 不达标→暂停 | — |
| R4: Wash Trading风险 | 风险分>0.7 | 暂停 | 24h |
| R5: 单交易所敞口 | ≤权益25% | 超额→拒绝 | — |
| R6: 总返利敞口 | ≤权益30% | 超额→拒绝 | — |
| R7: 期望价值比 | ≥0.5% | 不达标→警告 | — |
| R8: 活动截止风险 | 剩余<3天=critical | 禁止新开仓 | — |
| R9: 日亏损熔断 | ≥权益3% | 暂停 | 24h |
| R10: 费率突变 | 变化>50% | 暂停+审核 | 6h |

熔断器：
- 触发规则后自动进入冷却期
- 冷却期内所有新交易被阻止
- 状态通过 DB 持久化（重启后保持生效）
- 支持手动解除熔断
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

from .models import RiskCheckAction, RiskCheckResult

logger = logging.getLogger(__name__)


# 各规则触发后的冷却时间（秒）
RULE_COOLDOWN_SECONDS = {
    "R1": 86400,      # 24h
    "R2": 7 * 86400,  # 7天
    "R4": 86400,      # 24h
    "R9": 86400,      # 24h
    "R10": 21600,     # 6h
}


class CircuitBreaker:
    """
    熔断器 — 管理规则触发状态和冷却期。
    状态持久化到 DB 的 rebate_performance_logs 表（借用 data 字段）。
    """

    def __init__(self):
        # {rule_id: {"triggered_at": float, "cooldown_until": float, "reason": str}}
        self._breakers: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    def trip(self, rule_id: str, reason: str = ""):
        """触发熔断器"""
        cooldown = RULE_COOLDOWN_SECONDS.get(rule_id, 0)
        if cooldown <= 0:
            return  # 该规则无冷却期

        now = time.time()
        self._breakers[rule_id] = {
            "triggered_at": now,
            "cooldown_until": now + cooldown,
            "reason": reason,
        }
        logger.warning(
            f"[CircuitBreaker] 触发: {rule_id} | 冷却至 "
            f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(now + cooldown))} | {reason}"
        )
        self._persist()

    def is_tripped(self, rule_id: str = "") -> bool:
        """检查是否有活跃熔断"""
        now = time.time()
        if rule_id:
            breaker = self._breakers.get(rule_id)
            return breaker is not None and breaker["cooldown_until"] > now
        # 检查所有
        return any(b["cooldown_until"] > now for b in self._breakers.values())

    def get_active_breakers(self) -> Dict[str, Dict[str, Any]]:
        """获取所有活跃的熔断器状态"""
        now = time.time()
        return {
            rule_id: {
                "triggered_at": b["triggered_at"],
                "cooldown_until": b["cooldown_until"],
                "remaining_seconds": max(0, b["cooldown_until"] - now),
                "reason": b["reason"],
            }
            for rule_id, b in self._breakers.items()
            if b["cooldown_until"] > now
        }

    def reset(self, rule_id: str = ""):
        """手动解除熔断"""
        if rule_id:
            self._breakers.pop(rule_id, None)
            logger.info(f"[CircuitBreaker] 手动解除: {rule_id}")
        else:
            self._breakers.clear()
            logger.info("[CircuitBreaker] 全部解除")
        self._persist()

    def cleanup_expired(self):
        """清理已过期的熔断状态"""
        now = time.time()
        expired = [k for k, v in self._breakers.items() if v["cooldown_until"] <= now]
        for k in expired:
            del self._breakers[k]
        if expired:
            self._persist()

    def load_from_db(self):
        """从 DB 恢复熔断状态"""
        if self._loaded:
            return
        try:
            from backend.database.connection import SessionLocal
            db = SessionLocal()
            try:
                from backend.database.models import RebatePerformanceLogDB
                # 使用特殊 position_id 前缀标记熔断器状态记录
                row = db.query(RebatePerformanceLogDB).filter(
                    RebatePerformanceLogDB.position_id == "__circuit_breaker_state__"
                ).order_by(RebatePerformanceLogDB.id.desc()).first()

                if row and row.close_reason:
                    saved = json.loads(row.close_reason)
                    now = time.time()
                    # 仅恢复未过期的熔断
                    for rule_id, state in saved.items():
                        if state.get("cooldown_until", 0) > now:
                            self._breakers[rule_id] = state

                    if self._breakers:
                        logger.info(
                            f"[CircuitBreaker] 从DB恢复 {len(self._breakers)} 个活跃熔断"
                        )
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[CircuitBreaker] DB加载失败: {e}")

        self._loaded = True

    def _persist(self):
        """将当前熔断状态写入 DB"""
        try:
            from backend.database.connection import SessionLocal, sqlite_write_commit
            from backend.database.models import RebatePerformanceLogDB

            db = SessionLocal()
            try:
                # Upsert: 删除旧记录，写新记录
                db.query(RebatePerformanceLogDB).filter(
                    RebatePerformanceLogDB.position_id == "__circuit_breaker_state__"
                ).delete()

                entry = RebatePerformanceLogDB(
                    position_id="__circuit_breaker_state__",
                    strategy_type="CB",
                    total_pnl=0.0,
                    total_rebate=0.0,
                    total_points=0.0,
                    hold_hours=0.0,
                    close_reason=json.dumps(self._breakers, default=str),
                )
                db.add(entry)
                sqlite_write_commit(db, label="circuit_breaker_persist")
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[CircuitBreaker] 持久化失败: {e}")


class RebateRiskGate:
    """返利套利专项风控 — 10条规则 + 熔断器"""

    # ── 风控阈值（可被 config 覆盖）──
    MAX_DAILY_VOLUME_PER_EXCHANGE = 10_000.0     # R1: 日最大刷量/所 (USD)
    MAX_WEEKLY_VOLUME_PER_EXCHANGE = 50_000.0    # R2: 周最大刷量/所 (USD)
    MIN_ACTIVE_DAYS_PER_WEEK = 2                  # R3: 最少活跃天数/周
    MAX_WASH_TRADE_SCORE = 0.7                     # R4: 刷量风险阈值
    MAX_SINGLE_EXCHANGE_EXPOSURE_PCT = 0.25       # R5: 单所敞口上限
    MAX_TOTAL_REBATE_EXPOSURE_PCT = 0.30          # R6: 总返利敞口上限
    MIN_VOLUME_VALUE_RATIO = 0.005                # R7: 最低价值比 0.5%
    CAMPAIGN_CRITICAL_DAYS = 3                     # R8: 活动截止临界天数
    MAX_DAILY_LOSS_PCT = 0.03                      # R9: 日亏损熔断 3%
    MAX_FEE_CHANGE_PCT = 0.50                      # R10: 费率突变阈值 50%

    def __init__(self):
        self.circuit_breaker = CircuitBreaker()
        self._strategy_overrides: Dict[str, Dict[str, Any]] = {}
        self._load_config()

    def _load_config(self):
        """从 YAML 加载风控阈值"""
        try:
            from backend.config.rebate_config_loader import rebate_config
            if rebate_config:
                cfg = rebate_config.risk_gate
                self.MAX_DAILY_VOLUME_PER_EXCHANGE = cfg.max_daily_volume_per_exchange
                self.MAX_WEEKLY_VOLUME_PER_EXCHANGE = cfg.max_weekly_volume_per_exchange
                self.MIN_ACTIVE_DAYS_PER_WEEK = cfg.min_active_days_per_week
                self.MAX_WASH_TRADE_SCORE = cfg.wash_trade_threshold
                self.MAX_SINGLE_EXCHANGE_EXPOSURE_PCT = cfg.single_exchange_exposure_pct
                self.MAX_TOTAL_REBATE_EXPOSURE_PCT = cfg.total_rebate_exposure_pct
                self.MIN_VOLUME_VALUE_RATIO = cfg.min_value_ratio
                self.CAMPAIGN_CRITICAL_DAYS = cfg.campaign_deadline_critical_days
                self.MAX_DAILY_LOSS_PCT = cfg.daily_loss_circuit_breaker_pct
                self.MAX_FEE_CHANGE_PCT = cfg.fee_change_alert_pct
        except Exception as e:
            logger.debug(f"[RiskGate] Config load fallback: {e}")
        # Load per-strategy risk overrides from config
        self._load_strategy_overrides()

    def _load_strategy_overrides(self):
        """从配置加载策略级风控覆盖"""
        try:
            from backend.config.rebate_config_loader import rebate_config
            if rebate_config and hasattr(rebate_config, 'strategies'):
                for key, item in rebate_config.strategies.items():
                    if hasattr(item, 'params') and isinstance(item.params, dict):
                        overrides = item.params.get("risk_overrides")
                        if overrides and isinstance(overrides, dict):
                            strategy_id = key.split("_")[0]  # e.g. "S8_asterdex_rh" -> "S8"
                            self._strategy_overrides[strategy_id] = overrides
        except Exception as e:
            logger.debug(f"[RiskGate] Strategy overrides load fallback: {e}")

    def apply_strategy_overrides(self, strategy_id: str, overrides: Dict[str, Any]) -> None:
        """运行时设置策略级风控覆盖"""
        self._strategy_overrides[strategy_id] = overrides
        logger.info(f"[RiskGate] Applied overrides for {strategy_id}: {overrides}")

    def get_strategy_overrides(self) -> Dict[str, Dict[str, Any]]:
        """获取所有策略级风控覆盖"""
        return dict(self._strategy_overrides)

    def initialize(self):
        """初始化熔断器状态（从 DB 恢复）"""
        self.circuit_breaker.load_from_db()
        self.circuit_breaker.cleanup_expired()

    def check_pre_trade(
        self,
        strategy_type: str,
        exchange: str,
        size_usd: float,
        account_equity: float,
        context: Dict[str, Any],
    ) -> RiskCheckResult:
        """
        交易前风控检查 (R1-R10 全量 + 熔断器)

        Args:
            strategy_type: 策略类型 (S1-S8)
            exchange: 交易所名称
            size_usd: 拟交易金额
            account_equity: 账户总权益
            context: 包含 volume/days/wash_score/exposure 等信息

        Returns:
            RiskCheckResult 包含是否通过和原因
        """
        # 策略级覆盖检查（可禁用特定策略）
        overrides = self._strategy_overrides.get(strategy_type, {})
        if overrides.get("enabled") is False:
            return RiskCheckResult(
                passed=False,
                action=RiskCheckAction.BLOCK,
                rule_id="S_OVERRIDE",
                reason=f"策略 {strategy_type} 已被独立禁用",
            )
        # 覆盖单策略最大仓位
        max_pos = overrides.get("max_position_usd")
        if max_pos is not None and size_usd > max_pos:
            return RiskCheckResult(
                passed=False,
                action=RiskCheckAction.BLOCK,
                rule_id="S_OVERRIDE",
                reason=f"策略 {strategy_type} 单笔限额 ${max_pos:.0f}, 当前 ${size_usd:.0f}",
            )

        # R11: 规则同步闸门。ExecutionAuthority 已有第一道拦截，这里作为风控层兜底。
        try:
            from backend.services.rebate_arb.rule_sync_gate import rule_sync_gate
            if rule_sync_gate.is_rebate_blocked(strategy_type, manual=False):
                return RiskCheckResult(
                    passed=False,
                    action=RiskCheckAction.BLOCK,
                    rule_id="R11",
                    reason=rule_sync_gate.block_reason(strategy_type),
                    details=rule_sync_gate.get_state(),
                )
        except Exception as e:
            logger.debug("[RiskGate] RuleSyncGate R11 skipped: %s", e)

        # 熔断器前置检查
        if self.circuit_breaker.is_tripped():
            active = self.circuit_breaker.get_active_breakers()
            first_rule = next(iter(active))
            remaining = active[first_rule]["remaining_seconds"]
            return RiskCheckResult(
                passed=False,
                action=RiskCheckAction.BLOCK,
                rule_id=first_rule,
                reason=f"熔断器生效中 ({first_rule}), 剩余 {remaining/3600:.1f}h",
                details=active,
            )

        if account_equity <= 0:
            return RiskCheckResult(
                passed=False,
                action=RiskCheckAction.EMERGENCY_STOP,
                rule_id="R0",
                reason="账户权益为零或负值",
            )

        results = self.check_all(account_equity, context)
        if not results["passed"]:
            # 触发熔断器
            blocked = results["blocked_result"]
            self.circuit_breaker.trip(blocked.rule_id, blocked.reason)
            return blocked

        # R5/R6 增量预检：
        # S8 的实际资金占用（保证金）在执行计划生成后才确定，预检阶段 size_usd
        # 可能是名义价值，单位与存量敞口（S8 按保证金计）不一致，跳过预检，
        # 由引擎在计划生成后调用 check_incremental_exposure 做同单位复核。
        # 非 S8 策略无杠杆概念，size_usd≈名义≈资金占用，可直接预检。
        if strategy_type != "S8":
            inc = self.check_incremental_exposure(
                strategy_type=strategy_type,
                exchange=exchange,
                exposure_usd=size_usd,
                account_equity=account_equity,
                context=context,
            )
            if not inc.passed:
                return inc

        return RiskCheckResult(passed=True, action=RiskCheckAction.PASS, rule_id="ALL")

    def check_incremental_exposure(
        self,
        strategy_type: str,
        exchange: str,
        exposure_usd: float,
        account_equity: float,
        context: Dict[str, Any],
    ) -> RiskCheckResult:
        """
        R5/R6 增量敞口检查（统一单位）。

        exposure_usd 必须与 context 中存量敞口同单位：
        - S8 等杠杆方向仓 = 保证金（资金占用）
        - 对冲/无杠杆策略 = 双腿名义价值合计
        与 engine._position_risk_exposure_usd 的计量口径保持一致，由调用方保证。
        """
        if account_equity <= 0:
            return RiskCheckResult(
                passed=False,
                action=RiskCheckAction.EMERGENCY_STOP,
                rule_id="R0",
                reason="账户权益为零或负值",
            )

        current_exposure = context.get("exchange_exposure", {}).get(exchange, 0.0)
        new_exposure = current_exposure + max(float(exposure_usd or 0), 0.0)
        if new_exposure / account_equity > self.MAX_SINGLE_EXCHANGE_EXPOSURE_PCT:
            return RiskCheckResult(
                passed=False,
                action=RiskCheckAction.BLOCK,
                rule_id="R5",
                reason=f"单所敞口 {new_exposure/account_equity:.1%} 超过 {self.MAX_SINGLE_EXCHANGE_EXPOSURE_PCT:.0%}",
                details={"exchange": exchange, "pct": new_exposure / account_equity},
            )

        total_rebate_exposure = context.get("total_rebate_exposure", 0.0)
        new_total = total_rebate_exposure + max(float(exposure_usd or 0), 0.0)
        if new_total / account_equity > self.MAX_TOTAL_REBATE_EXPOSURE_PCT:
            return RiskCheckResult(
                passed=False,
                action=RiskCheckAction.BLOCK,
                rule_id="R6",
                reason=f"总返利敞口 {new_total/account_equity:.1%} 超过 {self.MAX_TOTAL_REBATE_EXPOSURE_PCT:.0%}",
            )

        return RiskCheckResult(passed=True, action=RiskCheckAction.PASS, rule_id="ALL")

    def check_all(self, account_equity: float, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        综合风控评估 — 10条规则全量检查

        Returns:
            {"passed": bool, "warnings": [], "blocked_result": RiskCheckResult}
        """
        warnings = []
        blocked_result = None

        # R1: 日最大刷量
        daily_volumes = context.get("daily_volumes", {})
        for exchange, vol in daily_volumes.items():
            if vol > self.MAX_DAILY_VOLUME_PER_EXCHANGE:
                blocked_result = RiskCheckResult(
                    passed=False,
                    action=RiskCheckAction.BLOCK,
                    rule_id="R1",
                    reason=f"{exchange} 日刷量 ${vol:,.0f} 超过 ${self.MAX_DAILY_VOLUME_PER_EXCHANGE:,.0f}",
                    details={"exchange": exchange, "volume": vol},
                )
                break

        # R2: 周最大刷量
        if not blocked_result:
            weekly_volumes = context.get("weekly_volumes", {})
            for exchange, vol in weekly_volumes.items():
                if vol > self.MAX_WEEKLY_VOLUME_PER_EXCHANGE:
                    blocked_result = RiskCheckResult(
                        passed=False,
                        action=RiskCheckAction.BLOCK,
                        rule_id="R2",
                        reason=f"{exchange} 周刷量 ${vol:,.0f} 超过 ${self.MAX_WEEKLY_VOLUME_PER_EXCHANGE:,.0f}",
                        details={"exchange": exchange, "volume": vol},
                    )
                    break

        # R3: 活跃天数（Paper 验证模式跳过，否则首周无法开第一单）
        if not blocked_result and not context.get("paper_verification"):
            active_days = context.get("active_days_this_week", 0)
            if active_days < self.MIN_ACTIVE_DAYS_PER_WEEK:
                blocked_result = RiskCheckResult(
                    passed=False,
                    action=RiskCheckAction.BLOCK,
                    rule_id="R3",
                    reason=f"活跃天数 {active_days} < {self.MIN_ACTIVE_DAYS_PER_WEEK}",
                    details={"active_days": active_days},
                )

        # R4: Wash Trading 风险
        if not blocked_result:
            wash_score = context.get("wash_trade_score", 0.0)
            if wash_score > self.MAX_WASH_TRADE_SCORE:
                blocked_result = RiskCheckResult(
                    passed=False,
                    action=RiskCheckAction.BLOCK,
                    rule_id="R4",
                    reason=f"刷量风险分 {wash_score:.2f} > {self.MAX_WASH_TRADE_SCORE}",
                    details={"wash_score": wash_score},
                )

        # R5: 单交易所敞口 (context 级)
        if not blocked_result:
            exchange_exposure = context.get("exchange_exposure", {})
            for exchange, exp in exchange_exposure.items():
                if exp / account_equity > self.MAX_SINGLE_EXCHANGE_EXPOSURE_PCT:
                    blocked_result = RiskCheckResult(
                        passed=False,
                        action=RiskCheckAction.BLOCK,
                        rule_id="R5",
                        reason=f"{exchange} 敞口 {exp/account_equity:.1%} 超过限制",
                    )
                    break

        # R6: 总返利敞口
        if not blocked_result:
            total_rebate = context.get("total_rebate_exposure", 0.0)
            if total_rebate / account_equity > self.MAX_TOTAL_REBATE_EXPOSURE_PCT:
                blocked_result = RiskCheckResult(
                    passed=False,
                    action=RiskCheckAction.BLOCK,
                    rule_id="R6",
                    reason=f"总返利敞口 {total_rebate/account_equity:.1%} 超过限制",
                )

        # R7: 期望价值比
        volume_value_ratio = context.get("volume_value_ratio", 0.0)
        if volume_value_ratio < self.MIN_VOLUME_VALUE_RATIO and volume_value_ratio > 0:
            warnings.append(
                f"R7: 价值比 {volume_value_ratio:.4f} < {self.MIN_VOLUME_VALUE_RATIO}"
            )

        # R8: 活动截止风险
        if not blocked_result:
            campaign_deadline_days = context.get("campaign_deadline_days", 999)
            if campaign_deadline_days < self.CAMPAIGN_CRITICAL_DAYS:
                blocked_result = RiskCheckResult(
                    passed=False,
                    action=RiskCheckAction.BLOCK,
                    rule_id="R8",
                    reason=f"活动截止仅剩 {campaign_deadline_days} 天，禁止新开仓",
                )

        # R9: 日亏损熔断
        if not blocked_result:
            daily_loss_pct = context.get("daily_loss_pct", 0.0)
            if daily_loss_pct > self.MAX_DAILY_LOSS_PCT:
                blocked_result = RiskCheckResult(
                    passed=False,
                    action=RiskCheckAction.EMERGENCY_STOP,
                    rule_id="R9",
                    reason=f"日亏损 {daily_loss_pct:.1%} 超过熔断线 {self.MAX_DAILY_LOSS_PCT:.0%}",
                )

        # R10: 费率突变
        if not blocked_result:
            fee_change_pct = context.get("fee_change_pct", 0.0)
            if abs(fee_change_pct) > self.MAX_FEE_CHANGE_PCT:
                blocked_result = RiskCheckResult(
                    passed=False,
                    action=RiskCheckAction.BLOCK,
                    rule_id="R10",
                    reason=f"费率变化 {fee_change_pct:.1%} 超过阈值 {self.MAX_FEE_CHANGE_PCT:.0%}",
                )

        passed = blocked_result is None
        return {
            "passed": passed,
            "warnings": warnings,
            "blocked_result": blocked_result or RiskCheckResult(
                passed=True, action=RiskCheckAction.PASS, rule_id="ALL"
            ),
        }

    def check_capital(self, available: float, required: float) -> RiskCheckResult:
        """资金池检查"""
        if available < required:
            return RiskCheckResult(
                passed=False,
                action=RiskCheckAction.BLOCK,
                rule_id="capital",
                reason=f"可用资金 ${available:,.0f} < 所需 ${required:,.0f}",
            )
        return RiskCheckResult(passed=True, action=RiskCheckAction.PASS)

    def check_wash_trade(self, wash_score: float) -> RiskCheckResult:
        """刷量风险检查"""
        if wash_score > self.MAX_WASH_TRADE_SCORE:
            return RiskCheckResult(
                passed=False,
                action=RiskCheckAction.BLOCK,
                rule_id="R4",
                reason=f"刷量风险 {wash_score:.2f} 超阈值",
            )
        return RiskCheckResult(passed=True, action=RiskCheckAction.PASS)


# 模块级单例
rebate_risk_gate = RebateRiskGate()
