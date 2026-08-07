"""
D7: AI Factor Discovery Service — LLM 辅助因子发现

从决策复盘中学习错误模式，用 LLM 自动生成新的量化因子。
"""
import json, logging, os, re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

@dataclass
class ErrorPattern:
    symbol: str; side: str; exit_reason: str; pnl: float
    pnl_pct: float; market_regime: str; lesson: str; frequency: int = 1

@dataclass
class GeneratedFactor:
    factor_id: str; name: str; display_name: str; description: str
    category: str; subcategory: str; python_code: str; confidence: float = 0.5
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

class AIFactorDiscoveryService:
    MIN_RETROSPECTIVES = 30
    MAX_CANDIDATES = 5

    def __init__(self):
        self._last_discovery = None
        self._generated_count = 0

    def should_discover(self, retrospective_count: int) -> bool:
        if retrospective_count < self.MIN_RETROSPECTIVES:
            return False
        if self._last_discovery and (datetime.now(timezone.utc) - self._last_discovery).days < 3:
            return False
        return True

    def extract_error_patterns(self, db) -> List[ErrorPattern]:
        from backend.database.models import DecisionRetrospective
        retros = db.query(DecisionRetrospective).filter(
            DecisionRetrospective.was_correct == "no"
        ).order_by(DecisionRetrospective.created_at.desc()).limit(50).all()
        if not retros:
            retros = db.query(DecisionRetrospective).order_by(
                DecisionRetrospective.created_at.desc()).limit(50).all()
        patterns: Dict[Tuple, ErrorPattern] = {}
        for r in retros:
            key = (r.symbol, r.exit_reason, r.market_regime_at_exit or "unknown")
            if key in patterns:
                patterns[key].frequency += 1
                patterns[key].pnl += float(r.realized_pnl or 0)
            else:
                patterns[key] = ErrorPattern(
                    symbol=r.symbol, side=r.side, exit_reason=r.exit_reason,
                    pnl=float(r.realized_pnl or 0), pnl_pct=float(r.pnl_pct or 0),
                    market_regime=r.market_regime_at_exit or "unknown",
                    lesson=r.lesson_learned or "", frequency=1)
        return sorted(patterns.values(), key=lambda x: x.frequency, reverse=True)[:10]

    def build_discovery_prompt(self, patterns: List[ErrorPattern]) -> str:
        ptext = "\n".join([
            f"- {p.symbol} {p.side}: {p.exit_reason} 亏{p.pnl:.0f}({p.pnl_pct:+.2f}%) "
            f"regime={p.market_regime} x{p.frequency}"
            for p in patterns
        ])
        return f"""你是一个量化交易因子挖掘专家。基于以下实盘亏损模式，生成1-3个新因子。

错误模式:
{ptext}

每个因子用 Python pandas/numpy，输入 pd.DataFrame(open/high/low/close/volume)，返回 pd.Series 值域[-1,+1]。
因子ID: ai_gen_<缩写>

JSON输出: {{"factors":[{{"factor_id":"ai_gen_xxx","name":"English","display_name":"中文","description":"逻辑","category":"technical/composite/behavioral/sentiment/derivatives","subcategory":"momentum/trend/volatility/volume/mean_reversion/contrarian","python_code":"def calculate(self, data):\\n    ...\\n    return result","confidence":0.6}}]}}
只输出JSON。"""

    @staticmethod
    def _parse_llm_json(raw: str) -> dict:
        text = (raw or "").strip()
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            cleaned = (
                text.replace("\n", " ")
                .replace("\r", " ")
                .replace("\t", " ")
                .replace(""", '"').replace(""", '"')
                .replace("'", "'").replace("'", "'")
            )
            return json.loads(cleaned)

    def call_llm_for_factor_discovery(self, patterns) -> List[GeneratedFactor]:
        try:
            from backend.services.llm_config_service import (
                get_llm_config_for_usage,
                call_llm_api_sync,
            )
            config = get_llm_config_for_usage("factor_mining")
            if not config or not config.api_key:
                logger.warning("[AIFactor] 无可用 LLM 配置（factor_mining），跳过本轮")
                return []
            resp_data = call_llm_api_sync(
                config,
                messages=[{"role": "user", "content": self.build_discovery_prompt(patterns)}],
                response_format={"type": "json_object"},
                max_tokens=3000,
                temperature=0.5,
                caller="ai_factor_discovery",
            )
            resp = None
            if resp_data:
                choices = resp_data.get("choices") or []
                if choices:
                    resp = (choices[0].get("message") or {}).get("content")
            if not resp:
                logger.warning("[AIFactor] LLM 空响应（已跳过本轮）")
                return []
            data = self._parse_llm_json(resp)
            return [GeneratedFactor(
                factor_id=it["factor_id"], name=it["name"],
                display_name=it["display_name"], description=it["description"],
                category=it["category"], subcategory=it["subcategory"],
                python_code=it["python_code"], confidence=it.get("confidence", 0.5))
                for it in data.get("factors", [])]
        except json.JSONDecodeError as e:
            logger.warning(f"[AIFactor] LLM JSON 解析失败（已跳过本轮）: {e}")
            return []
        except Exception as e:
            logger.warning(f"[AIFactor] LLM 调用失败（已跳过本轮）: {e}")
            return []

    @staticmethod
    def _to_class_identifier(name: str, fallback: str) -> str:
        """把 LLM 给出的人类可读因子名（如 "ADX Trend Strength"、
        "Volume-Price Alignment"）转成合法的 Python 类名（PascalCase）。

        2026-07-06 修复根因：此前代码直接把 factor.name 塞进
        `class {factor.name}(BaseFactor):`，只要名字里带空格/连字符/括号
        （LLM 几乎总是这么起名）就会产出语法错误的 .py 文件，加载时被
        FactorLoader 静默跳过（历史上导致 253 个已生成因子文件全部失效）。
        """
        words = re.findall(r"[A-Za-z0-9]+", name or "")
        if not words:
            words = re.findall(r"[A-Za-z0-9]+", fallback or "") or ["Factor"]
        ident = "".join(w if w.isupper() else w.capitalize() for w in words)
        if not ident or not ident[0].isalpha():
            ident = "F" + ident
        return ident

    @staticmethod
    def _esc(value: str) -> str:
        """转义将被塞进双引号字符串字面量的文本，防止 description/name
        里出现的引号或反斜杠打断生成的代码。"""
        return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")

    def _render_factor_source(self, factor: GeneratedFactor) -> str:
        """把 GeneratedFactor 渲染成最终写入磁盘的完整因子源码。

        单一来源：inject_factor 写文件、validate_generated_factor 语法校验
        都必须用这同一份渲染结果，否则会像历史 bug 那样——校验的是渲染前的
        代码片段，渲染后模板自身引入的语法错误完全没人检查。
        """
        class_name = self._to_class_identifier(factor.name, factor.factor_id)
        # 方法体缩进 4 空格（类方法应与 get_metadata 同级），历史上误用了
        # 8 空格，导致 calculate() 被嵌套进 get_metadata() 内部、根本不是
        # 类的方法，因子加载后调用 calculate 会直接 AttributeError。
        import textwrap as _tw
        # 断点③修复：先 dedent 再 indent，防止 python_code 自带缩进导致 8 空格嵌套
        dedented = _tw.dedent(factor.python_code.strip())
        indented_code = _tw.indent(dedented, "    ")
        display_name = self._esc(factor.display_name)
        description = self._esc(factor.description)
        name = self._esc(factor.name)
        return f'''"""AI因子: {display_name} | 置信:{factor.confidence:.0%} | {description}"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class {class_name}(BaseFactor):
    """{description}"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="{factor.factor_id}",
            name="{name}",
            display_name="{display_name}",
            description="{description}",
            category="{factor.category}",
            subcategory="{factor.subcategory}",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

{indented_code}
'''
# 断点③修复：indented_code 必须是 4 空格缩进（类方法级）。
# 旧模板的 textwrap.indent("    ") + python_code 自带缩进 → 8 空格嵌套进 get_metadata。
# 修复：先 dedent python_code 再 indent 4 空格，确保 calculate 与 get_metadata 同级。

    def validate_generated_factor(self, factor: GeneratedFactor) -> bool:
        forbidden = ["os.system", "subprocess", "eval(", "exec(", "__import__",
                     "open(", "shutil", "socket", "requests"]
        for kw in forbidden:
            if kw in factor.python_code.lower():
                logger.warning(f"[AIFactor] {factor.factor_id} 含禁止调用")
                return False
        try:
            # 2026-07-06 修复：校验最终渲染后的完整源码（而非渲染前的代码
            # 片段），否则模板自身的 bug（如类名非法）永远不会被这层校验拦下。
            full_code = self._render_factor_source(factor)
            compile(full_code, f"<{factor.factor_id}>", "exec")
        except SyntaxError as e:
            logger.warning(f"[AIFactor] {factor.factor_id} 语法错: {e}")
            return False
        return True

    def inject_factor(self, factor: GeneratedFactor, output_dir: str) -> bool:
        """注入 AI 生成因子到文件（2026-06-18 修复：目录+模板+装饰器；
        2026-07-06 修复：非法类名 + 缩进错误，见 _render_factor_source）。

        修复了以下致命 Bug：
        1. 目录错位：写入路径对齐到 services/factor_engine/factors/ai_generated/
        2. 缩进破损：python_code 现在正确缩进到方法体内
        3. 缺装饰器：加上 @register_factor() 使因子能被 FactorLoader 发现
        4. 类名非法：factor.name 含空格/连字符时不再是合法 Python 标识符
        """
        os.makedirs(output_dir, exist_ok=True)
        full_code = self._render_factor_source(factor)
        fp = os.path.join(output_dir, f"{factor.factor_id}.py")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(full_code)
        logger.info(f"[AIFactor] 注入: {factor.factor_id} -> {fp}")
        return True

    def run_discovery_cycle(self, db) -> Dict:
        try:
            from backend.database.models import DecisionRetrospective
            cnt = db.query(DecisionRetrospective).count()
            if not self.should_discover(cnt):
                return {"status": "skipped", "reason": f"条件不足(复盘={cnt})"}
            patterns = self.extract_error_patterns(db)
            if not patterns: return {"status": "skipped", "reason": "无模式"}
            candidates = self.call_llm_for_factor_discovery(patterns)
            if not candidates: return {"status": "failed", "reason": "无候选"}
            # 2026-06-18 修复：目录对齐到 services/factor_engine/factors/ai_generated/
            # （原错位到 backend/factor_engine/，FactorLoader 扫不到）
            output_dir = os.path.join(
                os.path.dirname(__file__),
                "factor_engine", "factors", "ai_generated",
            )
            injected = []
            for c in candidates[:self.MAX_CANDIDATES]:
                if self.validate_generated_factor(c):
                    if self.inject_factor(c, output_dir):
                        injected.append(c.factor_id)
                        self._generated_count += 1
            self._last_discovery = datetime.now(timezone.utc)
            result = {"status": "completed", "patterns": len(patterns),
                    "candidates": len(candidates), "injected": injected,
                    "total": self._generated_count}
            if injected:
                try:
                    from backend.services.factor_engine.base_factors import factor_engine
                    n = factor_engine.hot_reload()
                    result["hot_reload_added"] = n
                except Exception as _hr_err:
                    logger.warning(f"[AIFactor] 热加载失败: {_hr_err}")
            return result
        except Exception as e:
            logger.error(f"[AIFactor] 失败: {e}", exc_info=True)
            return {"status": "error", "reason": str(e)[:200]}

ai_factor_discovery = AIFactorDiscoveryService()
