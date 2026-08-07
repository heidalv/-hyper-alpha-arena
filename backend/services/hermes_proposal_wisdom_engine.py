"""Hermes Layer 1: 提案智慧积累引擎

从已验证的提案中提取参数效果模式，构建可检索的智慧库。
通过 proposal_id 关联主库 opencode_evolution_proposals 表。
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from backend.services.hermes_db import (
    hermes_execute,
    hermes_fetchall,
    hermes_fetchone,
    hermes_executemany,
    get_main_session,
)

logger = logging.getLogger(__name__)

# 市场状态推断规则
_MARKET_REGIME_KEYWORDS = {
    "trending_up": ["bull", "uptrend", "涨", "多"],
    "trending_down": ["bear", "downtrend", "跌", "空"],
    "ranging": ["range", "sideways", "震荡", "横盘"],
    "volatile": ["volatile", "高波动", "turmoil", "崩"],
}


class ProposalWisdomEngine:
    """Layer 1: 提案智慧积累引擎。

    核心职责：
    1. 从 paper_validated/rolled_back 提案中提取结构化智慧
    2. 聚合更新 param_effect_patterns 模式库（EMA 平滑 + 时间衰减）
    3. 输出 wisdom_context 文本供 prompt 注入
    """

    # ──── 公共 API ────

    def extract_wisdom_from_proposal(self, proposal_id: int) -> Optional[int]:
        """从单个已验证提案中提取智慧记录。幂等：已提取则跳过。"""
        db = get_main_session()
        try:
            from backend.database.models import OpenCodeEvolutionProposalDB

            row = db.query(OpenCodeEvolutionProposalDB).filter(
                OpenCodeEvolutionProposalDB.id == proposal_id
            ).first()
            if not row:
                logger.warning("[Hermes:L1] 提案 %d 不存在", proposal_id)
                return None

            # 幂等：Hermes 库已有记录则跳过（主库无 wisdom_extracted 列）
            existing = hermes_fetchone(
                "SELECT id FROM proposal_wisdom_records WHERE proposal_id=? LIMIT 1",
                (proposal_id,),
            )
            if existing:
                return None

            st = row.status or ""
            if st not in ("paper_validated", "rolled_back"):
                return None

            # 解析 after_json
            try:
                after = json.loads(row.after_json or "{}")
            except Exception:
                after = {}

            verdict = after.get("verdict", "?")
            focus = after.get("focus", "global")
            eval_metrics = after.get("eval_metrics") or {}
            baseline_perf = after.get("baseline_perf") or {}

            # 向后兼容：优先读新 schema（eval_metrics 子键），缺失时回退到
            # 旧版扁平 schema（after_json 直接存 total_pnl/total_closed，
            # baseline_perf 存 avg_pnl）。否则历史 96 条已验证提案因无
            # eval_metrics 键被全部读成 0，质量门 100% 误杀。
            after_avg_pnl, baseline_avg_pnl, wr_delta = self._extract_pnl_impact(
                after, eval_metrics, baseline_perf
            )
            pnl_impact = after_avg_pnl - baseline_avg_pnl
            pnl_change_ratio = float(eval_metrics.get("pnl_change_ratio") or 0)

            # 提取关键参数操作
            try:
                payload = json.loads(row.proposal_json or "{}")
                patches = payload.get("patches") or []
            except Exception:
                patches = []

            params_ops = self._extract_param_ops(patches)
            accept, accept_reason = self._should_accept_wisdom(
                after, pnl_impact, params_ops, pnl_change_ratio
            )
            if not accept:
                logger.info(
                    "[Hermes:L1] 提案 %d 跳过: %s (PnL=%.4f verdict=%s)",
                    proposal_id, accept_reason, pnl_impact, verdict,
                )
                return None

            is_neutral_pattern = accept_reason == "neutral_pattern"
            if is_neutral_pattern:
                pnl_impact = 0.0

            # 推断市场状态
            market_condition = self._infer_market_condition(row)

            wisdom_id = None
            for op in params_ops:
                _op_conf = float(op.get("confidence", 0.5) or 0.5)
                if is_neutral_pattern:
                    _op_conf = min(_op_conf, 0.4)
                elif _op_conf < 0.5:
                    continue
                wid = hermes_execute(
                    """INSERT INTO proposal_wisdom_records
                       (proposal_id, outcome, focus, market_condition,
                        param_key, param_direction, param_delta_pct,
                        pnl_impact, win_rate_delta, confidence,
                        attribution_json, context_snapshot)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        proposal_id, verdict, focus, market_condition,
                        op["key"], op["direction"], op.get("delta_pct", 0),
                        round(pnl_impact, 4), round(wr_delta, 4),
                        _op_conf,
                        json.dumps(after.get("attribution", {}), ensure_ascii=False),
                        json.dumps({
                            "market_condition": market_condition,
                            "eval_metrics": eval_metrics,
                        }, ensure_ascii=False),
                    ),
                )
                wisdom_id = wid

            if wisdom_id:
                logger.info(
                    "[Hermes:L1] 提案 %d 智慧提取: verdict=%s focus=%s params=%d",
                    proposal_id, verdict, focus, len(params_ops),
                )
            return wisdom_id
        finally:
            db.close()

    def accumulate_pending_wisdom(self) -> int:
        """扫描所有未提取智慧的已验证提案，批量提取。返回新提取数量。"""
        db = get_main_session()
        try:
            # 清理低质量智慧（保留 neutral 模式型记录）
            _cleaned = hermes_execute(
                """DELETE FROM proposal_wisdom_records
                   WHERE ABS(COALESCE(pnl_impact, 0)) < 0.01
                   AND COALESCE(confidence, 0) <= 0.3
                   AND COALESCE(outcome, '') != 'neutral'"""
            )
            if _cleaned:
                logger.info("[Hermes:L1] 清理低质量智慧: %d 条", _cleaned)

            from backend.database.models import OpenCodeEvolutionProposalDB

            rows = (
                db.query(OpenCodeEvolutionProposalDB)
                .filter(
                    OpenCodeEvolutionProposalDB.status.in_(
                        ["paper_validated", "rolled_back"]
                    ),
                )
                .order_by(OpenCodeEvolutionProposalDB.id.desc())
                .limit(100)
                .all()
            )
            extracted = 0
            for row in rows:
                # 快速检查是否已提取
                existing = hermes_fetchone(
                    "SELECT id FROM proposal_wisdom_records WHERE proposal_id=?",
                    (row.id,),
                )
                if existing:
                    continue
                if self.extract_wisdom_from_proposal(row.id):
                    extracted += 1

            if extracted:
                logger.info("[Hermes:L1] 批量智慧积累: %d 条新记录", extracted)
            return extracted
        finally:
            db.close()

    def build_wisdom_context(
        self,
        *,
        param_key: Optional[str] = None,
        market_condition: Optional[str] = None,
        focus: Optional[str] = None,
        limit: int = 15,
    ) -> str:
        """构建智慧上下文文本，供 LLM prompt 注入。"""
        conditions = ["1=1"]
        params = []
        if param_key:
            conditions.append("param_key=?")
            params.append(param_key)
        if market_condition:
            conditions.append("market_condition=?")
            params.append(market_condition)
        if focus:
            conditions.append("focus=?")
            params.append(focus)

        where = " AND ".join(conditions)
        records = hermes_fetchall(
            f"""SELECT outcome, param_key, param_direction, pnl_impact,
                       market_condition, confidence, 1 as single
                FROM proposal_wisdom_records
                WHERE {where}
                ORDER BY ABS(pnl_impact) DESC, confidence DESC
                LIMIT ?""",
            tuple(params) + (limit,),
        )

        if not records:
            return ""

        lines = [
            "## 历史提案智慧（从已验证提案中提炼的参数调整经验）\n",
        ]
        for r in records:
            direction_label = {"increase": "调高", "decrease": "调低", "": "修改"}.get(
                r.get("param_direction", ""), "修改"
            )
            outcome_label = {
                "improved": "✅ 改善",
                "degraded": "❌ 恶化",
                "neutral": "➖ 中性",
            }.get(r.get("outcome", ""), "?")
            pnl = r.get("pnl_impact", 0) or 0
            market = r.get("market_condition", "未知市况")
            lines.append(
                f"- [{outcome_label}] {direction_label} `{r.get('param_key','?')}` → "
                f"每笔 PnL ${pnl:+.2f}（市况: {market}，置信度: {r.get('confidence',0.5):.0%}）"
            )

        return "\n".join(lines)

    def get_top_patterns(self, *, min_samples: int = 3) -> List[Dict[str, Any]]:
        """获取高置信度参数效果模式。"""
        return hermes_fetchall(
            """SELECT param_key, market_condition, direction, outcome,
                      sample_count, avg_pnl_impact, confidence_avg,
                      pattern_summary
               FROM param_effect_patterns
               WHERE sample_count >= ?
               ORDER BY ABS(avg_pnl_impact) * confidence_avg DESC
               LIMIT 20""",
            (min_samples,),
        )

    def update_pattern_library(self) -> int:
        """从 wisdom_records 聚合更新 param_effect_patterns。

        使用 EMA 平滑（α=0.3）+ 时间衰减（7 天半衰期）防止旧数据支配。
        """
        rows = hermes_fetchall(
            """SELECT param_key, market_condition, param_direction AS direction, outcome,
                      COUNT(*) as cnt, AVG(pnl_impact) as avg_pnl,
                      AVG(win_rate_delta) as avg_wr, AVG(confidence) as avg_conf
               FROM proposal_wisdom_records
               WHERE param_key IS NOT NULL
               GROUP BY param_key, market_condition, param_direction
               HAVING cnt >= 2"""
        )

        updated = 0
        for r in rows:
            existing = hermes_fetchone(
                """SELECT id, sample_count, avg_pnl_impact, confidence_avg
                   FROM param_effect_patterns
                   WHERE param_key=? AND market_condition=? AND direction=?""",
                (r["param_key"], r["market_condition"], r["direction"]),
            )
            alpha = 0.3  # EMA 平滑系数
            new_count = int(r["cnt"])
            new_pnl = float(r["avg_pnl"] or 0)
            new_conf = float(r["avg_conf"] or 0.5)

            if existing:
                old_count = int(existing.get("sample_count", 0))
                old_pnl = float(existing.get("avg_pnl_impact", 0))
                old_conf = float(existing.get("confidence_avg", 0.5))
                # EMA 平滑
                smooth_pnl = old_pnl * (1 - alpha) + new_pnl * alpha
                smooth_conf = old_conf * (1 - alpha) + new_conf * alpha
                total_count = old_count + new_count
                hermes_execute(
                    """UPDATE param_effect_patterns
                       SET sample_count=?, avg_pnl_impact=?, avg_win_rate_delta=?,
                           confidence_avg=?, last_updated=datetime('now'),
                           decay_factor=?
                       WHERE id=?""",
                    (
                        total_count,
                        round(smooth_pnl, 4),
                        round(float(r["avg_wr"] or 0), 4),
                        round(smooth_conf, 4),
                        round(self._compute_decay(existing.get("last_updated", "")), 4),
                        existing["id"],
                    ),
                )
            else:
                # 首次插入
                try:
                    hermes_execute(
                        """INSERT INTO param_effect_patterns
                           (param_key, market_condition, direction, outcome,
                            sample_count, avg_pnl_impact, avg_win_rate_delta,
                            confidence_avg, last_updated)
                           VALUES (?,?,?,?,?,?,?,?,datetime('now'))""",
                        (
                            r["param_key"], r["market_condition"], r["direction"],
                            r["outcome"], new_count,
                            round(new_pnl, 4), round(float(r["avg_wr"] or 0), 4),
                            round(new_conf, 4),
                        ),
                    )
                except Exception:
                    # UNIQUE 冲突，跳过
                    pass
            updated += 1

        if updated:
            logger.info("[Hermes:L1] 模式库更新: %d 条模式", updated)
        return updated

    # ──── 私有辅助 ────

    def _should_accept_wisdom(
        self,
        after: Dict[str, Any],
        pnl_impact: float,
        patches: List[Dict[str, Any]],
        pnl_change_ratio: float,
    ) -> tuple:
        """分级质量门：PnL 显著 / verdict+ratio / neutral 模式型。"""
        params_ops = self._extract_param_ops(patches)
        if not params_ops:
            return False, "no_patches"

        if abs(pnl_impact) >= 0.01:
            return True, "pnl_delta"

        verdict = (after.get("verdict") or "").lower()
        if verdict in ("improved", "degraded") and abs(pnl_change_ratio) >= 0.05:
            return True, "verdict_ratio"

        if verdict == "neutral" and len(params_ops) >= 1:
            return True, "neutral_pattern"

        return False, "quality_gate"

    def _extract_pnl_impact(
        self,
        after: Dict[str, Any],
        eval_metrics: Dict[str, Any],
        baseline_perf: Dict[str, Any],
    ) -> tuple:
        """提取每笔期望收益变化与胜率变化。

        兼容两种 after_json schema：
        - 新 schema：eval_metrics 子键直接给出 after_avg_pnl / baseline_avg_pnl
        - 旧 schema：扁平存放 total_pnl + total_closed，baseline_perf 存
          avg_pnl（或 total_pnl/total_closed）。无 eval_metrics 时据此推导。

        返回 (after_avg_pnl, baseline_avg_pnl, win_rate_delta)。
        """
        def _safe_float(v: Any, default: float = 0.0) -> float:
            try:
                return float(v) if v is not None else default
            except (TypeError, ValueError):
                return default

        has_eval_metrics_key = "eval_metrics" in after

        # ── after_avg_pnl：新 schema 优先；无 eval_metrics 键时才扁平推导 ──
        after_avg_pnl = _safe_float(eval_metrics.get("after_avg_pnl"))
        if not has_eval_metrics_key and after_avg_pnl == 0.0:
            total_pnl = _safe_float(after.get("total_pnl"))
            total_closed = int(_safe_float(after.get("total_closed"), 0) or 0)
            if total_closed > 0:
                after_avg_pnl = total_pnl / total_closed

        # ── baseline_avg_pnl ──
        baseline_avg_pnl = _safe_float(eval_metrics.get("baseline_avg_pnl"))
        if not has_eval_metrics_key and baseline_avg_pnl == 0.0:
            baseline_avg_pnl = _safe_float(baseline_perf.get("avg_pnl"))
            if baseline_avg_pnl == 0.0:
                baseline_avg_pnl = _safe_float(baseline_perf.get("avg_pnl_per_trade"))
            if baseline_avg_pnl == 0.0:
                b_pnl = _safe_float(baseline_perf.get("total_pnl"))
                b_closed = int(_safe_float(baseline_perf.get("total_closed"), 0) or 0)
                if b_closed > 0:
                    baseline_avg_pnl = b_pnl / b_closed

        # ── 胜率变化 ──
        wr_after = _safe_float(after.get("win_rate"))
        wr_baseline = _safe_float(baseline_perf.get("win_rate"))
        wr_delta = wr_after - wr_baseline

        return after_avg_pnl, baseline_avg_pnl, wr_delta

    def _infer_market_condition(self, row) -> str:
        """从 proposal 上下文中推断市场状态。"""
        try:
            after = json.loads(row.after_json or "{}")
            baseline = json.loads(row.baseline_json or "{}")
            # 尝试从 context_snapshot 获取
            for src in [after, baseline]:
                snap = src.get("context_snapshot", {}) or {}
                regime = snap.get("market_regime", "")
                if regime:
                    return regime
        except Exception:
            pass
        return "ranging"

    def _extract_param_ops(
        self, patches: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """从 patches 列表提取参数操作。

        阶段2(S2-10b) 增强：patches 通常只有 {type,key,value,reason} 没有
        old_value，方向推断依赖 reason 文本里的旧值描述（如
        "max_daily_trades=12" / "从 0.20 降到 0.18"），否则 direction 恒空，
        param_effect_patterns 无法支撑参数域扩展。
        """
        ops = []
        for p in patches:
            if not isinstance(p, dict):
                continue
            key = p.get("key") or p.get("path") or ""
            if not key:
                continue
            ptype = (p.get("type") or "tuning").lower()
            if ptype == "shadow_py":
                continue

            old_val = p.get("old_value")
            new_val = p.get("value")

            direction = ""
            delta_pct = 0.0
            try:
                ov = float(old_val) if old_val is not None else None
                nv = float(new_val) if new_val is not None else None
                if ov is None and nv is not None:
                    # patches 无 old_value → 从 reason 文本解析旧值
                    ov = self._infer_old_value_from_reason(
                        key, nv, p.get("reason") or ""
                    )
                if ov is not None and nv is not None and ov != 0:
                    delta_pct = (nv - ov) / abs(ov)
                    direction = "increase" if nv > ov else "decrease"
            except (ValueError, TypeError):
                pass

            ops.append({
                "key": str(key),
                "direction": direction,
                "delta_pct": round(delta_pct, 4),
                "confidence": float(p.get("confidence", 0.5) or 0.5),
            })
        return ops

    def _infer_old_value_from_reason(
        self, key: str, new_value: float, reason: str
    ) -> Optional[float]:
        """从 reason 文本推断参数旧值（LLM 生成的调整理由）。

        优先精确匹配 ``<key>=<旧值>``（Hermes reason 的惯用格式，如
        "max_daily_trades=12"），其次中文"从 X 升/降到 Y"句式。
        """
        import re
        if not reason:
            return None

        # 模式 A: key=旧值 / key＝旧值
        m = re.search(rf"{re.escape(key)}\s*[=＝]\s*(-?[\d.]+)", reason)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass

        # 模式 B: 从 X 升到/降到 Y（value 为新值，方向由措辞区分）
        for pat in (
            r"从\s*(-?[\d.]+)\s*(?:升到|升至|提高到|上调|增加到)",
            r"从\s*(-?[\d.]+)\s*(?:降到|降至|下调|降低到|减少到)",
        ):
            m = re.search(pat, reason)
            if m:
                try:
                    return float(m.group(1))
                except ValueError:
                    continue

        # 模式 C: 当前 X（Hermes reason 惯用表述，如 "当前 12，+20% 上限…"）
        m = re.search(r"当前\s*[-—~]?\s*(-?[\d.]+)", reason)
        if m:
            try:
                old = float(m.group(1))
                # 防误判："当前" 之后紧跟的数值应近似旧值；与 new_value 相等视为无信息
                if abs(old - new_value) > 1e-9:
                    return old
            except ValueError:
                pass

        # 模式 D: X→Y 箭头（任一侧等于新值则另一侧为旧值，如 "12→10"）
        m = re.search(r"(-?[\d.]+)\s*[→→]\s*(-?[\d.]+)", reason)
        if m:
            try:
                a, b = float(m.group(1)), float(m.group(2))
                if abs(a - new_value) < 1e-9:
                    return b
                if abs(b - new_value) < 1e-9:
                    return a
            except ValueError:
                pass
        return None


    def _compute_decay(self, last_updated: str) -> float:
        """计算时间衰减因子（7 天半衰期）。"""
        if not last_updated:
            return 1.0
        try:
            from datetime import datetime as dt
            lu = dt.fromisoformat(last_updated.replace("Z", "+00:00"))
            now = dt.now(timezone.utc)
            days = (now - lu).total_seconds() / 86400
            return math.exp(-days * math.log(2) / 7)  # 7天半衰期
        except Exception:
            return 1.0


# 全局单例
proposal_wisdom = ProposalWisdomEngine()
