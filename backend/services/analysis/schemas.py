# -*- coding: utf-8 -*-
"""各任务族的输出 JSON 契约（轻量校验，不引第三方 schema 库）。

统一约定（所有任务共有，供交叉验证比对）：
  direction    bullish / bearish / neutral（→ +1 / -1 / 0）
  strength     0–10 的整数或小数
  confidence   0–1
  key_factors  ≤ 8 条简短依据（用于重叠度计算）
  summary      ≤ 400 字的结论

任务专有字段见 TASK_SCHEMAS；validate() 只做「必填 + 类型 + 值域」三件事，
不改写模型输出（保真 → 落库 → 事后评分）。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

DIRECTIONS = ("bullish", "bearish", "neutral")
REGIMES = ("trend_up", "trend_down", "range", "high_vol", "low_liquidity", "unknown")
LANE_VERDICTS = ("keep", "scale_up", "scale_down", "shadow", "stop")
POSITION_ACTIONS = ("none", "no_new_long", "no_new_short", "reduce", "tighten_stops", "hedge")
ARBITER_VERDICTS = ("A", "B", "merge", "neither")

COMMON_REQUIRED: Dict[str, str] = {
    "direction": "enum:direction",
    "strength": "number:0:10",
    "confidence": "number:0:1",
    "key_factors": "list:str",
    "summary": "str",
}

# 未注册任务的兜底模板。
# [2026-09-04] 原兜底是 {k: "..." for k in COMMON_REQUIRED}，把 direction/strength/
# confidence/key_factors 一律渲染成字符串 "..." 塞进 system prompt 当契约 —— 模型越
# 照抄，类型错得越彻底（实测 DeepSeek 与 qwen3 同时四个字段全错、consensus 恒为
# skipped）。兜底必须给出类型正确的示例。
COMMON_TEMPLATE: Dict[str, Any] = {
    "direction": "bullish|bearish|neutral",
    "strength": 0,
    "confidence": 0.0,
    "key_factors": ["..."],
    "summary": "...",
}

TASK_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "daily_brief": {
        "required": {
            **COMMON_REQUIRED,
            "regime": "enum:regime",
            "regime_confidence": "number:0:1",
            "bucket_weights": "weights:trend,cashflow,research",
            "symbol_views": "list:dict",
            "risks": "list:str",
        },
        "template": {
            "direction": "bullish|bearish|neutral",
            "strength": 0,
            "confidence": 0.0,
            "regime": "trend_up|trend_down|range|high_vol|low_liquidity|unknown",
            "regime_confidence": 0.0,
            "bucket_weights": {"trend": 0.6, "cashflow": 0.3, "research": 0.1},
            "symbol_views": [{"symbol": "BTCUSDT", "direction": "bullish|bearish|neutral", "strength": 0, "horizon_hours": 24, "reason": "..."}],
            "key_factors": ["..."],
            "risks": ["..."],
            "summary": "...",
        },
    },
    "trend_chart_review": {
        "required": {
            **COMMON_REQUIRED,
            "structure": "str",
            "timeframe_alignment": "str",
            "key_levels": "list:dict",
            "invalidation": "str",
            "position_advice": "str",
        },
        "template": {
            "direction": "bullish|bearish|neutral",
            "strength": 0,
            "confidence": 0.0,
            "structure": "当前K线结构一句话（如：高位宽幅震荡、重心下移，疑似顶部派发）",
            "timeframe_alignment": "1w/1d/4h 三周期方向如何共振或背离",
            "key_levels": [{"level": 0.0, "type": "support|resistance", "note": "..."}],
            "invalidation": "出现什么价格行为时本判断作废",
            "position_advice": "none|no_new_long|no_new_short|reduce|hold",
            "key_factors": ["..."],
            "summary": "...",
        },
    },
    "event_impact": {
        "required": {
            **COMMON_REQUIRED,
            "half_life_hours": "number:0:720",
            "affected_symbols": "list:str",
            "hedging_window_hours": "number:0:720",
            "position_adjustments": "list:dict",
        },
        "template": {
            "direction": "bullish|bearish|neutral",
            "strength": 0,
            "confidence": 0.0,
            "half_life_hours": 0,
            "affected_symbols": ["BTCUSDT"],
            "hedging_window_hours": 0,
            "position_adjustments": [{"symbol": "BTCUSDT", "action": "none|no_new_long|no_new_short|reduce|tighten_stops|hedge", "reason": "..."}],
            "analogues": [{"event": "...", "outcome": "..."}],
            "key_factors": ["..."],
            "summary": "...",
        },
    },
    "weekly_review": {
        "required": {
            **COMMON_REQUIRED,
            "lane_assessments": "list:dict",
            "experiments": "list:dict",
        },
        "template": {
            "direction": "bullish|bearish|neutral",
            "strength": 0,
            "confidence": 0.0,
            "lane_assessments": [{"lane": "...", "verdict": "keep|scale_up|scale_down|shadow|stop", "evidence": ["..."]}],
            "experiments": [
                {
                    "title": "...",
                    "hypothesis": "...",
                    "change": {"config_key": "new_value"},
                    "expected_metrics": [{"metric": "net_bp", "op": ">", "threshold": 0, "scope": "lane:..."}],
                    "window_hours": 168,
                    "rollback_condition": "...",
                }
            ],
            "key_factors": ["..."],
            "summary": "...",
        },
    },
    "param_search": {
        "required": {**COMMON_REQUIRED, "candidates": "list:dict"},
        "template": {
            "direction": "neutral",
            "strength": 0,
            "confidence": 0.0,
            "candidates": [{"params": {"key": "value"}, "rationale": "...", "expected_effect": "..."}],
            "key_factors": ["..."],
            "summary": "...",
        },
    },
    "timing": {
        "required": {
            **COMMON_REQUIRED,
            "regime": "enum:regime",
            "bucket_weights": "weights:trend,cashflow,research",
            "engine_capital": "list:dict",
        },
        "template": {
            "direction": "bullish|bearish|neutral",
            "strength": 0,
            "confidence": 0.0,
            "regime": "trend_up|trend_down|range|high_vol|low_liquidity|unknown",
            "bucket_weights": {"trend": 0.6, "cashflow": 0.3, "research": 0.1},
            "engine_capital": [{"engine": "E1|E2|E5|E3", "weight": 0.0, "rationale": "..."}],
            "counterfactual": {"last_week_if_followed_bp": 0, "note": "..."},
            "key_factors": ["..."],
            "summary": "...",
        },
    },
    "arbiter": {
        "required": {
            "verdict": "enum:arbiter",
            "direction": "enum:direction",
            "strength": "number:0:10",
            "confidence": "number:0:1",
            "rationale": "str",
            "key_factors": "list:str",
        },
        "template": {
            "verdict": "A|B|merge|neither",
            "direction": "bullish|bearish|neutral",
            "strength": 0,
            "confidence": 0.0,
            "rationale": "...",
            "key_factors": ["..."],
        },
    },
    "gateway_test": {
        "required": {"direction": "enum:direction", "strength": "number:0:10", "confidence": "number:0:1", "summary": "str"},
        "template": {"direction": "neutral", "strength": 0, "confidence": 0.0, "summary": "..."},
    },
    # [2026-09-04] signal_review / anomaly 此前未注册，走兜底模板 → 输出必然 schema
    # 失败，这两个 agent 的 LLM 层开启即 100% 产不出结论。按各自 prompt 的真实意图补齐。
    "signal_review": {
        "required": {**COMMON_REQUIRED, "source_findings": "list:dict"},
        "template": {
            "direction": "bullish|bearish|neutral",
            "strength": 0,
            "confidence": 0.0,
            "source_findings": [
                {
                    "source": "...",
                    "likely_cause": "...",
                    "falsifiable_hypothesis": "...",
                    "suggested_check": "...",
                }
            ],
            "key_factors": ["..."],
            "summary": "...",
        },
    },
    # [2026-09-04] 新闻标注。**不复用 COMMON_REQUIRED**：这里的 direction 是
    # −1~+1 的连续值（news_events.impact_direction 的量纲），strength 是 1~5，
    # 与通用契约的 enum direction / 0~10 strength 都不是一回事，混用会让模型
    # 输出的量纲对不上库表，标注全部作废。
    "news_annotate": {
        "required": {
            "direction": "number:-1:1",
            "strength": "number:1:5",
            "duration": "str",
            "symbols": "list:str",
            "category": "str",
            "confidence": "number:0:1",
            "summary": "str",
        },
        "template": {
            "direction": 0.0,
            "strength": 1,
            "duration": "short|medium|long",
            "symbols": ["BTC"],
            "category": "regulation|exchange|tech|macro|whale|blackswan|general",
            "confidence": 0.0,
            "summary": "一句话摘要",
        },
    },
    "anomaly": {
        "required": {**COMMON_REQUIRED, "root_causes": "list:dict", "counter_scenarios": "list:str"},
        "template": {
            "direction": "bullish|bearish|neutral",
            "strength": 0,
            "confidence": 0.0,
            "root_causes": [{"channel": "...", "cause": "...", "evidence": "..."}],
            "counter_scenarios": ["..."],
            "key_factors": ["..."],
            "summary": "...",
        },
    },
    # [2026-09-05] 中长线交易论题。direction 仍用分析层 bullish/bearish/neutral，
    # 主脑里映射为 long/short。缺现价/K 线时 recommend_open 必须 false。
    # direction 是行情判断，不是「开不开」；观望应 recommend_open=false，
    # 只有多空证据真正打平才写 neutral。
    # [2026-09-06] 模板示例改为「入场区已触及 → recommend_open=true」，
    # 避免默认 False 把模型 priming 成永远不开。
    "midlong_thesis": {
        "required": {
            **COMMON_REQUIRED,
            "recommend_open": "bool",
            "should_close": "bool",
            "invalidation": "inv_px",
            "missing_evidence": "list:str",
            "sl_pct": "number:0:0.5",
            "tp_pct": "number:0:1",
            "thesis_summary": "str",
            # entry_zone 不进 required：旧票/漏写不应整票 schema 失败变单模型；
            # 模板+提示词仍强要求；有 zone 且现价落入时代码会 promote_open。
        },
        "template": {
            "direction": "bullish|bearish|neutral",
            "strength": 6,
            "confidence": 0.72,
            "recommend_open": True,
            "should_close": False,
            "invalidation": {"price": 2400.0, "condition": "4h收盘跌破结构低点则论题作废"},
            "entry_zone": {"low": 2450.0, "high": 2480.0},
            "missing_evidence": [],
            "sl_pct": 0.05,
            "tp_pct": 0.10,
            "thesis_summary": "多头结构完好，现价已回踩入场区，允许小仓试多",
            "key_factors": ["结构多头", "现价在入场区"],
            "summary": "入场区已触及，recommend_open=true",
        },
    },
}


def direction_to_int(v: Any) -> int:
    if isinstance(v, (int, float)):
        return 1 if v > 0 else (-1 if v < 0 else 0)
    s = str(v or "").strip().lower()
    if s in ("bullish", "long", "up", "positive", "+1", "1"):
        return 1
    if s in ("bearish", "short", "down", "negative", "-1"):
        return -1
    # [轮140 2026-09-20] 中文方向词归一：实测（core.analysis_runs 最近 30 条）
    # 有模型直接返回 `direction="中性"` —— 不在白名单里就被静默当 0（中性），
    # 与"模型明确说了中性"无法区分（噪声掩盖）。补中文映射；仍未知的保持 0。
    if s in ("看多", "做多", "偏多", "多", "上涨"):
        return 1
    if s in ("看空", "做空", "偏空", "空", "下跌"):
        return -1
    return 0


def _check(kind: str, val: Any) -> Optional[str]:
    parts = kind.split(":")
    t = parts[0]
    if t == "str":
        return None if isinstance(val, str) and val.strip() else "应为非空字符串"
    if t == "bool":
        return None if isinstance(val, bool) else "应为布尔"
    if t == "number":
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            return "应为数字"
        lo, hi = float(parts[1]), float(parts[2])
        return None if lo <= float(val) <= hi else f"应在 [{lo}, {hi}]"
    if t == "enum":
        allowed = {"direction": DIRECTIONS, "regime": REGIMES, "arbiter": ARBITER_VERDICTS}[parts[1]]
        allowed_ci = {a.lower() for a in allowed}
        if parts[1] == "direction":
            # 交易论题常写 long/short；与 direction_to_int 对齐，避免单票被契约误杀。
            allowed_ci.update(("long", "short", "up", "down", "positive", "negative"))
        return None if isinstance(val, str) and val.strip().lower() in allowed_ci else f"应为 {'/'.join(allowed)}"
    if t == "inv":
        if isinstance(val, str) and val.strip():
            return None
        if isinstance(val, dict) and any(
            str(val.get(k) or "").strip() for k in ("condition", "price", "invalidation_price")
        ):
            return None
        return "应为非空字符串或含 condition/price 的对象"
    if t == "inv_px":
        # 中长线论题：必须有可机读数字失效价，否则哨兵无法打穿即平。
        if not isinstance(val, dict):
            return "应为含 price 的对象"
        try:
            px = float(val.get("price") or val.get("invalidation_price") or 0)
        except (TypeError, ValueError):
            px = 0.0
        if px <= 0:
            return "必须含 price>0"
        return None
    if t == "entry_zone":
        if not isinstance(val, dict):
            return "应为含 low/high 的对象"
        try:
            lo = float(val.get("low") or 0)
            hi = float(val.get("high") or 0)
        except (TypeError, ValueError):
            return "low/high 应为数字"
        if lo <= 0 or hi <= 0:
            return "low/high 必须 >0"
        if lo > hi:
            return "low 不得大于 high"
        return None
    if t == "list":
        if not isinstance(val, list):
            return "应为数组"
        inner = parts[1]
        if inner == "str" and not all(isinstance(x, str) for x in val):
            return "数组元素应为字符串"
        if inner == "dict" and not all(isinstance(x, dict) for x in val):
            return "数组元素应为对象"
        return None
    if t == "weights":
        keys = parts[1].split(",")
        if not isinstance(val, dict):
            return "应为对象"
        try:
            vals = [float(val.get(k, 0.0)) for k in keys]
        except Exception:
            return "权重应为数字"
        if any(v < 0 for v in vals):
            return "权重不能为负"
        s = sum(vals)
        return None if 0.9 <= s <= 1.1 else f"权重和应≈1（当前 {s:.2f}）"
    return None


def validate(task: str, obj: Any) -> Tuple[bool, List[str]]:
    """返回 (是否通过, 错误列表)。未知任务只校验 COMMON_REQUIRED。"""
    if not isinstance(obj, dict):
        return False, ["输出不是 JSON 对象"]
    spec = TASK_SCHEMAS.get(task, {"required": COMMON_REQUIRED})
    errors: List[str] = []
    for key, kind in spec["required"].items():
        if key not in obj:
            errors.append(f"缺少字段 {key}")
            continue
        err = _check(kind, obj[key])
        if err:
            errors.append(f"{key}: {err}")
    return (not errors), errors


def template_text(task: str) -> str:
    spec = TASK_SCHEMAS.get(task)
    tpl = spec["template"] if spec else COMMON_TEMPLATE
    return json.dumps(tpl, ensure_ascii=False, indent=2)


# 契约存在性标志：ModelGateway 据此判断调用方是否已自行拼接契约，避免重复注入。
CONTRACT_MARK = "输出要求：只返回一个 JSON 对象"


def _kind_desc(kind: str) -> str:
    """把 required 里的机器格式（如 number:0:10）翻成给模型看的一句话约束。"""
    parts = kind.split(":")
    t = parts[0]
    if t == "inv":
        return "非空字符串，或含 condition/price 的对象"
    if t == "inv_px":
        return "对象，必须含 price>0，可兼有 condition"
    if t == "entry_zone":
        return "对象 {low,high}，均为 >0 的数字且 low<=high；现价落区内则应 recommend_open=true"
    if t == "str":
        return "非空字符串"
    if t == "bool":
        return "布尔（true/false，不要加引号）"
    if t == "number":
        return f"数字（不加引号），取值范围 [{parts[1]}, {parts[2]}]"
    if t == "enum":
        allowed = {"direction": DIRECTIONS, "regime": REGIMES,
                   "arbiter": ARBITER_VERDICTS}.get(parts[1], ())
        return "枚举，只能取：" + " / ".join(allowed)
    if t == "list":
        return "字符串数组" if len(parts) > 1 and parts[1] == "str" else "对象数组"
    if t == "weights":
        return f"对象，键为 {parts[1]}；各值非负且总和≈1"
    return kind


def constraints_text(task: str) -> str:
    spec = TASK_SCHEMAS.get(task)
    req = spec["required"] if spec else COMMON_REQUIRED
    lines = [f"- {k}：{_kind_desc(v)}" for k, v in req.items()]
    # midlong：entry_zone 虽非硬必填，仍写入约束段，催模型给数字入场区。
    if task == "midlong_thesis":
        lines.append(
            "- entry_zone：" + _kind_desc("entry_zone")
            + "（强烈建议给出；现价落入区内时应 recommend_open=true）"
        )
    return "\n".join(lines)


def output_contract(task: str) -> str:
    """嵌入 system prompt 的输出契约段。

    [2026-09-04] 除模板外，additionally 渲染逐字段约束。此前契约只给一份示例
    JSON，取值范围**从未告诉过模型** —— `strength` 的模板值是 `0`，模型无从得知
    上限是 10。实测 qwen3:14b 蒙对、qwen2.5:7b 蒙出超范围值被判 schema 失败，
    于是交叉验证悄悄退化成单票（consensus 只有 0.35，且 comparison 显示
    single_vote），看起来像"两个模型有分歧"，实则是一票根本没进来。
    约束本来就写在 required 里，这里只是把它讲给模型听。
    """
    return (
        CONTRACT_MARK + "，不要 Markdown 代码块、不要任何解释性前后缀。"
        "字段与类型必须严格符合下面的模板（枚举值只能取模板中列出的选项；数字不要加引号）：\n"
        + template_text(task)
        + "\n\n各字段取值约束（务必遵守，超出范围会被判为无效输出）：\n"
        + constraints_text(task)
    )
