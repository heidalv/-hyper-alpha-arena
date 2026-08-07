"""
Strategic Analyst - 战略报告生成器

将宏观评估 + 新币扫描 + 战略记忆 融合为 StrategicReport：
1. 合并各子系统输出
2. 调用 QUICK 级 LLM 生成结构化战略总结
3. 持久化到数据库
4. 返回 StrategicReport 对象
"""

import logging
import json
from typing import List, Optional
from datetime import datetime

from .models import (
    MacroAssessment,
    NewCoinOpportunity,
    StrategicMemory,
    StrategicReport,
    CrossMarketCorrelation,
)
from .strategic_memory import StrategicMemorySystem

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM Prompt 模板
# ---------------------------------------------------------------------------
STRATEGIC_ANALYSIS_PROMPT = """你是一个专业的加密货币市场战略分析师。基于以下宏观数据，生成一份简洁的战略分析报告。

## 当前宏观环境
- 宏观体制: {regime} (风险偏好评分: {risk_on_score:.2f})
- S&P500: {spx_status} (日涨跌: {spx_change}%)
- 美元指数(DXY): {dxy_status} (日涨跌: {dxy_change}%)
- 沪深300: {csi300_status} (日涨跌: {csi300_change}%)
- 恐贪指数: {fear_greed}
- 联邦基金利率: {fed_rate}%
- BTC市值占比: {btc_dom}%

## 跨市场相关性
{correlations_text}

## 关键风险
{risks_text}

## 历史类似环境经验
{memories_text}

## 新币机会
{new_coins_text}

请按以下JSON格式输出（不要输出其他内容）：
{{
    "market_cycle_phase": "accumulation/bull_trend/bear_trend/distribution/transition",
    "macro_bias": "bullish/bearish/neutral",
    "macro_confidence": 0.0到1.0的数字,
    "risk_budget_adjustment": 0.5到1.5的数字(1.0为不调整),
    "recommended_direction": "long/short/neutral",
    "key_insights": ["洞察1", "洞察2", "洞察3"],
    "sp500_impact_summary": "美股对加密市场的影响分析(1-2句)",
    "china_market_impact_summary": "中国股市对加密市场的影响分析(1-2句)",
    "geopolitical_risks": ["风险1", "风险2"],
    "regulatory_outlook": "监管政策展望(1-2句)",
    "strategic_summary": "200字以内的战略总结"
}}
"""


class ReportGenerator:
    """
    战略报告生成器

    将宏观评估、新币扫描、战略记忆融合为结构化报告
    """

    def __init__(self, memory_system: StrategicMemorySystem):
        self.memory_system = memory_system

    def generate(
        self,
        macro_assessment: MacroAssessment,
        snapshot_data: dict,
        new_coin_opportunities: List[NewCoinOpportunity],
        cross_market_correlations: List[CrossMarketCorrelation] = None,
    ) -> StrategicReport:
        """
        生成战略报告

        Args:
            macro_assessment: 宏观评估结果
            snapshot_data: MacroSnapshot 的字典数据
            new_coin_opportunities: 新币机会列表
            cross_market_correlations: 跨市场相关性列表

        Returns:
            StrategicReport
        """
        report = StrategicReport(
            timestamp=datetime.utcnow(),
            macro_assessment=macro_assessment,
            new_coin_opportunities=new_coin_opportunities,
            cross_market_correlations=cross_market_correlations or [],
        )

        # 1. 检索相关记忆
        context_str = self._build_context_string(macro_assessment, snapshot_data)
        report.relevant_memories = self.memory_system.retrieve_relevant(
            context_str, top_k=3
        )

        # 2. 调用 LLM 生成分析（如果可用）
        llm_result = self._call_llm_analysis(
            macro_assessment, snapshot_data, report.relevant_memories,
            new_coin_opportunities, cross_market_correlations,
        )

        if llm_result:
            self._apply_llm_result(report, llm_result)
        else:
            # LLM 不可用时使用规则化回退
            self._rule_based_report(report, macro_assessment, snapshot_data)

        # 3. 计算数据质量评分
        report.data_quality_score = self._calculate_data_quality(snapshot_data)

        # 4. 持久化报告
        self._persist_report(report, snapshot_data)

        logger.info(
            f"[ReportGenerator] 报告生成: "
            f"phase={report.market_cycle_phase}, "
            f"bias={report.macro_bias}, "
            f"confidence={report.macro_confidence:.2f}, "
            f"risk_adj={report.risk_budget_adjustment:.2f}"
        )

        return report

    # -----------------------------------------------------------------------
    # LLM 调用
    # -----------------------------------------------------------------------

    def _call_llm_analysis(
        self,
        macro: MacroAssessment,
        snapshot: dict,
        memories: List[StrategicMemory],
        new_coins: List[NewCoinOpportunity],
        correlations: List[CrossMarketCorrelation],
    ) -> Optional[dict]:
        """调用 LLM 生成战略分析"""
        try:
            try:
                from backend.services.llm_config_service import get_llm_config, call_llm_api_sync
            except ImportError:
                from services.llm_config_service import get_llm_config, call_llm_api_sync
        except ImportError:
            logger.debug("[ReportGenerator] llm_config_service 不可用")
            return None

        try:
            config = get_llm_config()
            if not config:
                return None
        except Exception:
            return None

        # 构建 prompt
        prompt = STRATEGIC_ANALYSIS_PROMPT.format(
            regime=macro.regime,
            risk_on_score=macro.risk_on_score,
            spx_status=macro.spx_impact,
            spx_change=self._fmt_pct(snapshot.get("spx_change_pct")),
            dxy_status=macro.dxy_impact,
            dxy_change=self._fmt_pct(snapshot.get("dxy_change_pct")),
            csi300_status=macro.china_market_impact,
            csi300_change=self._fmt_pct(snapshot.get("csi300_change_pct")),
            fear_greed=self._fmt_val(snapshot.get("fear_greed_index")),
            fed_rate=self._fmt_val(snapshot.get("fed_funds_rate")),
            btc_dom=self._fmt_pct(snapshot.get("btc_dominance")),
            correlations_text=self._format_correlations(correlations),
            risks_text=self._format_risks(macro.key_risks),
            memories_text=self._format_memories(memories),
            new_coins_text=self._format_new_coins(new_coins),
        )

        messages = [
            {"role": "user", "content": prompt}
        ]

        try:
            response = call_llm_api_sync(
                config,
                messages=messages,
                temperature=0.3,
                max_tokens=1500,
                caller="strategic_analyst",
            )

            if response:
                content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
                if content:
                    return self._parse_llm_response(content)
        except Exception as e:
            logger.warning(f"[ReportGenerator] LLM 调用失败: {e}")

        return None

    def _parse_llm_response(self, content: str) -> Optional[dict]:
        """解析 LLM 的 JSON 响应"""
        # 去除可能的 markdown 代码块标记
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1])

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # 尝试提取 JSON 部分
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(content[start:end])
                except json.JSONDecodeError:
                    pass
            logger.warning("[ReportGenerator] LLM 响应 JSON 解析失败")
            return None

    def _apply_llm_result(self, report: StrategicReport, result: dict) -> None:
        """将 LLM 结果应用到报告"""
        report.market_cycle_phase = result.get("market_cycle_phase", "unknown")
        report.macro_bias = result.get("macro_bias", "neutral")
        report.macro_confidence = float(result.get("macro_confidence", 0.5))
        report.risk_budget_adjustment = float(np_clip(
            result.get("risk_budget_adjustment", 1.0), 0.5, 1.5
        ))
        report.recommended_direction = result.get("recommended_direction", "neutral")
        report.key_insights = result.get("key_insights", [])
        report.sp500_impact_summary = result.get("sp500_impact_summary", "")
        report.china_market_impact_summary = result.get("china_market_impact_summary", "")
        report.geopolitical_risks = result.get("geopolitical_risks", [])
        report.regulatory_outlook = result.get("regulatory_outlook", "")
        report.llm_analysis = result.get("strategic_summary", "")

    # -----------------------------------------------------------------------
    # 规则化回退（LLM 不可用时）
    # -----------------------------------------------------------------------

    def _rule_based_report(
        self,
        report: StrategicReport,
        macro: MacroAssessment,
        snapshot: dict,
    ) -> None:
        """规则化报告生成（LLM 不可用的回退方案）"""
        # 市场周期阶段
        regime = macro.regime
        phase_map = {
            "risk_on": "bull_trend",
            "risk_off": "bear_trend",
            "neutral": "accumulation",
            "transition": "transition",
        }
        report.market_cycle_phase = phase_map.get(regime, "unknown")

        # 宏观偏向
        score = macro.risk_on_score
        if score > 0.3:
            report.macro_bias = "bullish"
        elif score < -0.3:
            report.macro_bias = "bearish"
        else:
            report.macro_bias = "neutral"

        report.macro_confidence = macro.confidence
        report.recommended_direction = macro.impact_direction

        # 风险预算调整
        if macro.impact_direction == "bullish":
            report.risk_budget_adjustment = 1.0 + min(macro.impact_magnitude * 0.5, 0.3)
        elif macro.impact_direction == "bearish":
            report.risk_budget_adjustment = 1.0 - min(macro.impact_magnitude * 0.5, 0.4)
        else:
            report.risk_budget_adjustment = 1.0

        # 美股影响
        report.sp500_impact_summary = f"SPX日涨跌{self._fmt_pct(snapshot.get('spx_change_pct'))}，" \
                                       f"对加密市场影响: {macro.spx_impact}"

        # 中国股市影响
        report.china_market_impact_summary = f"沪深300日涨跌{self._fmt_pct(snapshot.get('csi300_change_pct'))}，" \
                                              f"对加密市场影响: {macro.china_market_impact}"

        # 关键洞察
        report.key_insights = macro.key_risks[:5]

        # 地缘风险
        report.geopolitical_risks = []
        report.regulatory_outlook = "数据不足，暂无监管展望"

    # -----------------------------------------------------------------------
    # 持久化
    # -----------------------------------------------------------------------

    def _persist_report(self, report: StrategicReport, snapshot: dict) -> None:
        """持久化报告到数据库（使用 AnalyticsSessionLocal）"""
        session = None
        try:
            try:
                from backend.database.connection import AnalyticsSessionLocal
            except ImportError:
                from database.connection import AnalyticsSessionLocal
            from .db_models import StrategicReportRecord, StrategicMacroSnapshot

            session = AnalyticsSessionLocal()

            # 保存宏观快照
            snap_record = StrategicMacroSnapshot(
                dxy_value=snapshot.get("dxy_value"),
                dxy_change_pct=snapshot.get("dxy_change_pct"),
                spx_close=snapshot.get("spx_close"),
                spx_change_pct=snapshot.get("spx_change_pct"),
                csi300_close=snapshot.get("csi300_close"),
                csi300_change_pct=snapshot.get("csi300_change_pct"),
                fed_funds_rate=snapshot.get("fed_funds_rate"),
                crypto_market_cap=snapshot.get("crypto_market_cap"),
                btc_dominance=snapshot.get("btc_dominance"),
                fear_greed_index=snapshot.get("fear_greed_index"),
                regime=report.macro_assessment.regime if report.macro_assessment else "unknown",
                risk_on_score=report.macro_assessment.risk_on_score if report.macro_assessment else 0.0,
                data_sources_status=json.dumps(snapshot.get("data_sources_status", {})),
            )
            session.add(snap_record)
            session.flush()

            # 保存报告
            report_record = StrategicReportRecord(
                report_type="regular",
                market_cycle_phase=report.market_cycle_phase,
                macro_bias=report.macro_bias,
                macro_confidence=report.macro_confidence,
                risk_budget_adjustment=report.risk_budget_adjustment,
                recommended_direction=report.recommended_direction,
                sp500_impact_summary=report.sp500_impact_summary,
                china_market_impact_summary=report.china_market_impact_summary,
                geopolitical_risks=json.dumps(report.geopolitical_risks),
                regulatory_outlook=report.regulatory_outlook,
                key_insights=json.dumps(report.key_insights),
                llm_analysis=report.llm_analysis,
                macro_snapshot_id=snap_record.id,
                new_coin_count=len(report.new_coin_opportunities),
                memory_count=len(report.relevant_memories),
                data_quality_score=report.data_quality_score,
            )
            session.add(report_record)
            session.commit()
            session.close()

            logger.info(f"[ReportGenerator] 报告已持久化: snapshot_id={snap_record.id}")
        except Exception as e:
            logger.warning(f"[ReportGenerator] 报告持久化失败: {e}")
            if session:
                try:
                    session.rollback()
                    session.close()
                except Exception:
                    pass

    # -----------------------------------------------------------------------
    # 辅助方法
    # -----------------------------------------------------------------------

    def _build_context_string(self, macro: MacroAssessment, snapshot: dict) -> str:
        """构建用于记忆检索的上下文字符串"""
        parts = [f"宏观体制:{macro.regime}"]
        if macro.impact_direction != "neutral":
            parts.append(f"影响方向:{macro.impact_direction}")
        if snapshot.get("fear_greed_index"):
            parts.append(f"恐贪指数:{snapshot['fear_greed_index']:.0f}")
        if snapshot.get("spx_change_pct"):
            parts.append(f"SPX:{snapshot['spx_change_pct']:.2f}%")
        if snapshot.get("dxy_change_pct"):
            parts.append(f"DXY:{snapshot['dxy_change_pct']:.2f}%")
        return " ".join(parts)

    def _calculate_data_quality(self, snapshot: dict) -> float:
        """计算数据质量评分"""
        key_fields = [
            "dxy_value", "spx_change_pct", "csi300_change_pct",
            "fear_greed_index", "btc_dominance", "fed_funds_rate",
        ]
        available = sum(1 for f in key_fields if snapshot.get(f) is not None)
        return float(available / len(key_fields))

    def _format_correlations(self, corrs: List[CrossMarketCorrelation]) -> str:
        if not corrs:
            return "暂无相关性数据"
        lines = []
        for c in corrs:
            r_30d = f"{c.correlation_30d:.3f}" if c.correlation_30d else "N/A"
            lines.append(f"- {c.pair_name}: 30日相关性={r_30d} (体制: {c.regime})")
        return "\n".join(lines)

    def _format_risks(self, risks: List[str]) -> str:
        if not risks:
            return "暂无显著风险"
        return "\n".join(f"- {r}" for r in risks)

    def _format_memories(self, memories: List[StrategicMemory]) -> str:
        if not memories:
            return "暂无相关历史经验"
        lines = []
        for m in memories:
            lines.append(f"- [{m.memory_type}] {m.lesson}")
        return "\n".join(lines)

    def _format_new_coins(self, coins: List[NewCoinOpportunity]) -> str:
        if not coins:
            return "暂无新币机会"
        lines = []
        for c in coins:
            lines.append(f"- {c.symbol}: 热度={c.hype_score:.0f}, 策略={c.recommended_strategy}")
        return "\n".join(lines)

    @staticmethod
    def _fmt_pct(val) -> str:
        if val is None:
            return "N/A"
        return f"{val:.2f}%"

    @staticmethod
    def _fmt_val(val) -> str:
        if val is None:
            return "N/A"
        return f"{val:.2f}"


def np_clip(val, min_val, max_val):
    """不依赖 numpy 的 clip"""
    return max(min_val, min(max_val, val))
