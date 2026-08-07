"""
套利机会生命周期监控器

跟踪套利机会的状态变化（new → active → expiring → expired），
检测资金费率反转信号，提供引擎状态查询。

Phase 2: 仅跟踪扫描结果，不跟踪实际仓位。
"""

import logging
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

from .models import ArbitrageOpportunity, ArbitrageStatus
from .opportunity_scanner import OpportunityScanner

logger = logging.getLogger(__name__)


class ArbitragePositionMonitor:
    """
    套利机会生命周期监控器

    线程安全，跟踪机会状态变化和资金费率历史。
    """

    # 机会存活时间（秒）
    OPPORTUNITY_TTL = 3600 * 4    # 4小时
    # 反转检测窗口
    REVERSAL_WINDOW = 3           # 连续3期
    # 历史记录上限
    MAX_OPPORTUNITY_LOG = 200

    def __init__(self, scanner: Optional[OpportunityScanner] = None):
        self._scanner = scanner or OpportunityScanner()
        self._opportunities: Dict[str, ArbitrageOpportunity] = {}
        self._lock = threading.Lock()
        self._opportunity_log: deque = deque(maxlen=self.MAX_OPPORTUNITY_LOG)
        self._scan_count: int = 0
        self._start_time: float = time.time()

    def update(
        self,
        symbols: List[str],
        snapshot: Any,
    ) -> List[ArbitrageOpportunity]:
        """
        执行一次扫描并更新跟踪状态

        Args:
            symbols: 交易对列表
            snapshot: UnifiedSnapshot 实例

        Returns:
            当前活跃的套利机会列表
        """
        current_ts = time.time()
        new_opps = self._scanner.scan_opportunities(symbols, snapshot)

        with self._lock:
            self._scan_count += 1
            new_ids = set()

            # 更新/新增机会
            for opp in new_opps:
                new_ids.add(opp.symbol)
                if opp.symbol in self._opportunities:
                    # 已跟踪 → 保持 active
                    existing = self._opportunities[opp.symbol]
                    if existing.status == ArbitrageStatus.EXPIRING:
                        # 重新出现 → 恢复 active
                        opp = ArbitrageOpportunity(
                            opportunity_id=opp.opportunity_id,
                            symbol=opp.symbol,
                            strategy=opp.strategy,
                            expected_annual_yield=opp.expected_annual_yield,
                            funding_snapshot=opp.funding_snapshot,
                            recommended_size=opp.recommended_size,
                            risk_score=opp.risk_score,
                            confidence=opp.confidence,
                            timestamp=opp.timestamp,
                        )
                self._opportunities[opp.symbol] = opp
                self._opportunity_log.append({
                    'symbol': opp.symbol,
                    'annual_yield': opp.expected_annual_yield,
                    'strategy': opp.strategy,
                    'risk_score': opp.risk_score,
                    'timestamp': current_ts,
                })

            # 不在新扫描中的机会 → 标记 expiring
            for sym, opp in list(self._opportunities.items()):
                if sym not in new_ids:
                    if opp.status == ArbitrageStatus.ACTIVE:
                        # 创建 expiring 版本
                        self._opportunities[sym] = ArbitrageOpportunity(
                            opportunity_id=opp.opportunity_id,
                            symbol=opp.symbol,
                            strategy=opp.strategy,
                            expected_annual_yield=opp.expected_annual_yield,
                            funding_snapshot=opp.funding_snapshot,
                            recommended_size=opp.recommended_size,
                            risk_score=opp.risk_score,
                            confidence=opp.confidence,
                            timestamp=opp.timestamp,
                            status=ArbitrageStatus.EXPIRING,
                        )

            # 清除过期的
            expired = []
            for sym, opp in self._opportunities.items():
                if (opp.status == ArbitrageStatus.EXPIRING and
                        current_ts - opp.timestamp > self.OPPORTUNITY_TTL):
                    expired.append(sym)
            for sym in expired:
                del self._opportunities[sym]

        return list(self._opportunities.values())

    def monitor_positions(self) -> List[Dict]:
        """
        检查资金费率反转，更新机会状态

        Returns:
            当前跟踪的所有机会状态列表
        """
        results = []
        with self._lock:
            for sym, opp in list(self._opportunities.items()):
                # 检查反转
                history = self._scanner.get_funding_history(sym)
                reversal_warning = self._check_reversal(history)

                results.append({
                    'symbol': sym,
                    'strategy': opp.strategy,
                    'annual_yield': opp.expected_annual_yield,
                    'risk_score': opp.risk_score,
                    'confidence': opp.confidence,
                    'status': opp.status.value,
                    'reversal_warning': reversal_warning,
                })

        return results

    def get_status(self) -> Dict:
        """获取引擎状态摘要"""
        with self._lock:
            opportunities = list(self._opportunities.values())
            return {
                'enabled': True,
                'total_scans': self._scan_count,
                'active_opportunities': len([o for o in opportunities if o.status == ArbitrageStatus.ACTIVE]),
                'expiring_opportunities': len([o for o in opportunities if o.status == ArbitrageStatus.EXPIRING]),
                'total_opportunities_found': len(self._opportunity_log),
                'uptime_seconds': time.time() - self._start_time,
            }

    def get_opportunity_history(
        self, symbol: Optional[str] = None, limit: int = 50,
    ) -> List[Dict]:
        """获取机会历史记录"""
        with self._lock:
            logs = list(self._opportunity_log)
        if symbol:
            logs = [l for l in logs if l['symbol'] == symbol]
        return logs[-limit:]

    def _check_reversal(self, history: List[float]) -> bool:
        """
        检测资金费率反转

        最近 REVERSAL_WINDOW 期费率方向与策略方向相反 → 反转警告
        """
        if len(history) < self.REVERSAL_WINDOW:
            return False
        recent = history[-self.REVERSAL_WINDOW:]
        # 检查最近几期是否全部同号（且与之前的趋势不同）
        if all(r > 0 for r in recent) or all(r < 0 for r in recent):
            # 检查是否与更早的历史方向不同
            if len(history) > self.REVERSAL_WINDOW + 5:
                earlier_avg = sum(history[-(self.REVERSAL_WINDOW + 5):-self.REVERSAL_WINDOW]) / 5
                recent_avg = sum(recent) / len(recent)
                return (earlier_avg > 0 and recent_avg < 0) or (earlier_avg < 0 and recent_avg > 0)
        return False


# ── 模块级单例 ──
arb_monitor = ArbitragePositionMonitor()
