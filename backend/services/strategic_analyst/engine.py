"""
Strategic Analyst - 核心引擎

单例模式，编排四个子系统：
1. MacroDataCollector - 宏观数据采集
2. MacroAnalysisEngine - 宏观关联分析
3. NewCoinScanner - 新币打新扫描
4. StrategicMemorySystem - 长期记忆系统

通过 ReportGenerator 生成 StrategicReport，
供 LongTermPlanner 和 MasterController 消费。
"""

import logging
import threading
from typing import Optional
from datetime import datetime

from .models import StrategicReport, MacroSnapshot
from .macro_data_collector import MacroDataCollector
from .macro_analysis_engine import MacroAnalysisEngine
from .new_coin_scanner import NewCoinScanner
from .strategic_memory import StrategicMemorySystem
from .report_generator import ReportGenerator

logger = logging.getLogger(__name__)


class StrategicAnalystEngine:
    """
    战略分析师引擎

    单例模式，对外的统一入口
    """

    def __init__(self):
        self.macro_collector = MacroDataCollector()
        self.macro_engine = MacroAnalysisEngine()
        self.new_coin_scanner = NewCoinScanner()
        self.memory_system = StrategicMemorySystem()
        self.report_generator = ReportGenerator(self.memory_system)

        self._latest_report: Optional[StrategicReport] = None
        self._report_lock = threading.Lock()
        self._last_analysis_time: Optional[datetime] = None

    # -----------------------------------------------------------------------
    # 核心方法
    # -----------------------------------------------------------------------

    def run_full_analysis(self) -> StrategicReport:
        """
        执行完整的战略分析流水线

        流程:
        1. 采集宏观数据 → MacroSnapshot
        2. 宏观分析 → MacroAssessment
        3. 新币扫描 → List[NewCoinOpportunity]
        4. 检索相关记忆 → List[StrategicMemory]
        5. 生成报告 → StrategicReport
        6. 缓存报告

        Returns:
            StrategicReport 战略报告
        """
        logger.info("[StrategicEngine] 开始执行完整战略分析...")

        # 1. 采集宏观数据
        snapshot = self.macro_collector.fetch_all()

        # 2. 宏观关联分析
        macro_assessment = self.macro_engine.analyze(snapshot)

        # 3. 新币扫描
        try:
            opportunities = self.new_coin_scanner.scan()
        except Exception as e:
            logger.warning(f"[StrategicEngine] 新币扫描失败: {e}")
            opportunities = []

        # 4. 生成报告（内部会检索记忆 + 调用 LLM）
        snapshot_dict = self._snapshot_to_dict(snapshot)
        report = self.report_generator.generate(
            macro_assessment=macro_assessment,
            snapshot_data=snapshot_dict,
            new_coin_opportunities=opportunities,
            cross_market_correlations=[],
        )

        # 5. 缓存报告
        with self._report_lock:
            self._latest_report = report
            self._last_analysis_time = datetime.utcnow()

        logger.info(
            f"[StrategicEngine] 战略分析完成: "
            f"phase={report.market_cycle_phase}, "
            f"bias={report.macro_bias}, "
            f"confidence={report.macro_confidence:.2f}, "
            f"risk_adj={report.risk_budget_adjustment:.2f}, "
            f"new_coins={len(report.new_coin_opportunities)}"
        )

        return report

    def get_latest_report(self) -> Optional[StrategicReport]:
        """
        获取最新缓存的战略报告

        Returns:
            StrategicReport 或 None（如果尚未执行过分析）
        """
        with self._report_lock:
            return self._latest_report

    def get_strategic_context_for_planner(self) -> Optional[dict]:
        """
        为 LongTermPlanner 提供战略上下文

        Returns:
            精简的战略上下文字典，可直接注入 PlanningResult
        """
        report = self.get_latest_report()
        if report is None:
            return None

        return {
            "macro_regime": report.macro_assessment.regime if report.macro_assessment else "unknown",
            "macro_bias": report.macro_bias,
            "macro_confidence": report.macro_confidence,
            "risk_budget_adjustment": report.risk_budget_adjustment,
            "recommended_direction": report.recommended_direction,
            "regime_transition_signal": (
                report.macro_assessment.regime_transition_signal
                if report.macro_assessment else False
            ),
            "key_insights": report.key_insights[:3],
            "data_quality_score": report.data_quality_score,
        }

    def get_strategic_prompt_section(self) -> str:
        """
        为 MasterController 生成战略分析 Prompt section

        Returns:
            格式化的战略分析文本，可直接注入 LLM prompt
        """
        report = self.get_latest_report()
        if report is None:
            return ""

        lines = ["### 战略分析（宏观 x 新币 x 经验记忆）"]

        # 宏观体制
        if report.macro_assessment:
            ma = report.macro_assessment
            lines.append(f"- 宏观体制: {ma.regime} (置信度: {report.macro_confidence:.0%})")
            lines.append(f"- 风险偏好评分: {ma.risk_on_score:.2f} (-1~+1)")
            lines.append(f"- 影响方向: {ma.impact_direction}, 强度: {ma.impact_magnitude:.2f}")
            if ma.regime_transition_signal:
                lines.append("- **体制转换信号: 检测到宏观环境可能发生转变**")

        # 跨市场
        if report.cross_market_correlations:
            lines.append("- 跨市场相关性:")
            for corr in report.cross_market_correlations:
                lines.append(f"  - {corr.pair_name}: 30日r={corr.correlation_30d:.3f} ({corr.regime})")

        # 风险预算调整
        adj = report.risk_budget_adjustment
        if adj != 1.0:
            direction = "提高" if adj > 1.0 else "降低"
            lines.append(f"- 风险预算调整: {direction} {abs(adj-1.0)*100:.0f}%")

        # 美股/中国股市影响
        if report.sp500_impact_summary:
            lines.append(f"- 美股影响: {report.sp500_impact_summary}")
        if report.china_market_impact_summary:
            lines.append(f"- 中国股市影响: {report.china_market_impact_summary}")

        # 新币机会
        if report.new_coin_opportunities:
            active = [o for o in report.new_coin_opportunities if o.recommended_strategy != "avoid"]
            if active:
                lines.append(f"- 新币机会: {', '.join(o.symbol for o in active)}")

        # 历史经验
        if report.relevant_memories:
            lines.append("- 历史经验:")
            for m in report.relevant_memories[:3]:
                lines.append(f"  - {m.lesson}")

        # LLM 分析总结
        if report.llm_analysis:
            lines.append(f"- 战略总结: {report.llm_analysis}")

        return "\n".join(lines)

    def store_macro_observation(
        self,
        observation: str,
        lesson: str,
        conditions: dict = None,
    ) -> None:
        """快捷方法：存储宏观观察"""
        self.memory_system.store_observation(
            memory_type="macro_lesson",
            market_context=self._get_current_market_context(),
            observation=observation,
            lesson=lesson,
            applicability_conditions=conditions,
            confidence=0.5,
        )

    def store_cycle_pattern(
        self,
        observation: str,
        lesson: str,
        conditions: dict = None,
    ) -> None:
        """快捷方法：存储周期模式"""
        self.memory_system.store_observation(
            memory_type="cycle_pattern",
            market_context=self._get_current_market_context(),
            observation=observation,
            lesson=lesson,
            applicability_conditions=conditions,
            confidence=0.5,
        )

    def run_memory_validation(self) -> int:
        """
        执行记忆验证（每日定时任务）

        Returns:
            处理的记忆数量
        """
        rules = self.memory_system.extract_rules()
        if rules:
            logger.info(f"[StrategicEngine] 提取了 {len(rules)} 条规则")
        return len(rules)

    def run_new_coin_scan(self) -> list:
        """
        单独执行新币扫描（每小时定时任务，不触发完整分析）

        Returns:
            新发现的机会列表
        """
        try:
            return self.new_coin_scanner.scan()
        except Exception as e:
            logger.warning(f"[StrategicEngine] 新币扫描异常: {e}")
            return []

    @property
    def last_analysis_time(self) -> Optional[datetime]:
        return self._last_analysis_time

    # -----------------------------------------------------------------------
    # 内部方法
    # -----------------------------------------------------------------------

    def _get_current_market_context(self) -> str:
        """获取当前市场上下文描述"""
        report = self.get_latest_report()
        if report and report.macro_assessment:
            return f"regime={report.macro_assessment.regime}, bias={report.macro_bias}"
        return "unknown"

    @staticmethod
    def _snapshot_to_dict(snapshot: MacroSnapshot) -> dict:
        """将 MacroSnapshot 转为字典"""
        return {
            "dxy_value": snapshot.dxy_value,
            "dxy_change_pct": snapshot.dxy_change_pct,
            "spx_close": snapshot.spx_close,
            "spx_change_pct": snapshot.spx_change_pct,
            "csi300_close": snapshot.csi300_close,
            "csi300_change_pct": snapshot.csi300_change_pct,
            "fed_funds_rate": snapshot.fed_funds_rate,
            "crypto_market_cap": snapshot.crypto_market_cap,
            "btc_dominance": snapshot.btc_dominance,
            "fear_greed_index": snapshot.fear_greed_index,
            "regime": snapshot.regime,
            "risk_on_score": snapshot.risk_on_score,
            "data_sources_status": snapshot.data_sources_status,
        }


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------
_engine_instance: Optional[StrategicAnalystEngine] = None
_engine_lock = threading.Lock()


def get_strategic_engine() -> StrategicAnalystEngine:
    """获取战略分析师引擎单例"""
    global _engine_instance
    if _engine_instance is None:
        with _engine_lock:
            if _engine_instance is None:
                _engine_instance = StrategicAnalystEngine()
    return _engine_instance
