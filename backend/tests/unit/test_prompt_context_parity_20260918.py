# -*- coding: utf-8 -*-
"""[F370 2026-09-18] **预览 ≠ 实盘**：两套 prompt 上下文构造器的变量集漂移。

## 事实（两条独立静态证据）

1. **AST 键集**：实盘 `PromptContextBuilder`（`prompt_context/` 包）产出 **44** 个键；
   预览 `_build_prompt_context`（`ai_decision_service.py:628`）产出 **83** 个键。
   ⚠️ **这个"仅预览键数"不可对外报**（含大量嵌套数据结构的 dict 键）。
2. **校准口径（会被引用的那个数）**：与**真实模板占位符**（22 个模板、49 个静态占位符）求交后，
   **"模板在用、实盘不给"= 6 个**：`news_section`（22/22 模板）、`environment`、
   `market_regime`、`market_regime_description`、`recent_trades_summary`、
   `selected_symbols_count`。
3. **全包 grep**：`prompt_context/*.py` 里 `news_section` / `factor_guidance` /
   `recent_trades_summary` / `decision_chain` **命中 0 次**，且 `BuildInput` **没有**
   `news_section` 字段 ⇒ 实盘构造器**不可能**提供它。

## 后果（为什么这是"可观测性谎言"）

实盘渲染是 `_tpl_text.format_map(SafeDict(context))`，而
`SafeDict.__missing__` 返回 `"N/A"` ⇒ **缺键静默变 N/A，不报错不打日志**；
模板的 `required_placeholders` 校验（:2339-2345）只检查"占位符**在模板文本里**存不存在"，
**从不检查"上下文是否提供了它"** —— 校验查的是错的一侧。

⇒ 实盘 LLM 决策的 prompt 里，**新闻区块（`news_section`）**与另外 5 个占位符恒为 "N/A"，
而**预览界面**（用户用来判断"AI 到底看到了什么"的窗口）却能显示真实内容。
这也解释了 §8 声明 1b 的 `{factor_guidance}`——它**不在**这 6 个之列
（不是任何模板的占位符），属另一条问题。

## 运行时核验的现状（如实标注）

`scripts/verify_live_prompt_context.py` 尝试直接调用该构造器取真实键集，但被
`builder.py:95` 的输入假设挡住（`normalized_symbol_metadata` 期望 dict、实测传入后
`.get("name")` 得到 str ⇒ `AttributeError`）。**故本结论标注为"静态双证据"**，
运行时确认列为待办。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

ADS = ROOT / "backend/services/ai_decision_service.py"
PC_PKG = ROOT / "backend/services/prompt_context"


def _live_keys() -> set:
    keys: set = set()
    for f in sorted(PC_PKG.glob("*.py")):
        src = f.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                    and isinstance(node.slice.value, str):
                keys.add(node.slice.value)
            elif isinstance(node, ast.Dict):
                for k in node.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        keys.add(k.value)
    return keys


def _preview_keys() -> set:
    src = ADS.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_build_prompt_context"), None)
    assert fn is not None, "未找到 _build_prompt_context"
    seg = ast.get_source_segment(src, fn) or ""
    keys: set = set()
    for node in ast.walk(ast.parse(seg)):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                and isinstance(node.slice.value, str):
            keys.add(node.slice.value)
    return keys


def test_safedict_silently_fills_na():
    """缺键 → "N/A" 且不报错 ⇒ 漂移是**静默**的（这是问题严重性的根源）。"""
    from backend.services.ai_decision_service import SafeDict
    assert SafeDict({})["anything_missing"] == "N/A"
    assert "{news_section}".format_map(SafeDict({})) == "N/A"


def test_news_section_used_by_templates_but_absent_from_live_builder():
    """`{news_section}` 出现在所有模板里，而实盘构造器包里**一次都不出现**。"""
    hits = 0
    for f in PC_PKG.glob("*.py"):
        hits += f.read_text(encoding="utf-8", errors="replace").count("news_section")
    assert hits == 0, "实盘构造器包突然出现 news_section ⇒ 可能已修复，请同步报告 §27"
    assert "news_section" not in _live_keys()
    # BuildInput 没有该字段（结构性证据）
    types_src = (PC_PKG / "types.py").read_text(encoding="utf-8")
    assert "news_section" not in types_src
    # 预览侧确实提供它（作为函数参数并写入上下文）
    ads = ADS.read_text(encoding="utf-8", errors="replace")
    assert "news_section: str" in ads
    assert "news_section" in _preview_keys()


def test_preview_provides_much_more_than_live():
    """AST 层面的"仅预览"键很多 —— 但**这个数字不能直接对外报**（见下一个用例）。"""
    live, prev = _live_keys(), _preview_keys()
    assert len(prev) > len(live), (len(prev), len(live))
    only_prev = prev - live
    assert len(only_prev) >= 20, (
        f"仅预览提供的键降到 {len(only_prev)} 个 ⇒ 可能已合流，请复核报告 §27 与待办 B11")


#: [F374 校准口径] 真实会渲染成 N/A 的占位符集合（模板在用 ∩ 实盘不给 ∩ 非动态族）
CALIBRATED_GAP = {
    "news_section",              # 22/22 模板都在用；来自预览的**调用方参数**
    "environment",               # BuildInput 已有字段，但实盘构造器未写进上下文
    "market_regime",
    "market_regime_description",
    "recent_trades_summary",
    "selected_symbols_count",
}


def test_calibrated_gap_is_six_not_fortyseven():
    """**决定性口径**：只有"真模板占位符"才算会渲染成 N/A 的缺口。

    我第一版把 `_build_prompt_context` 里**所有 dict 字面量键**都当成变量，
    于是把 `{"15m":…, "1h":…}` 这类嵌套数据结构的键也算进来，报出"47 个变量恒为 N/A"——
    **虚高约 7 倍**。校准后是 **6 个**。
    本用例锁住这个数字，防止再被引用成 AST 口径的粗数。
    """
    import re
    live, prev = _live_keys(), _preview_keys()
    only_prev = prev - live
    try:
        from sqlalchemy import text
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text("SET app.is_admin='on'"))
            rows = db.execute(text("SELECT template_text FROM prompt_templates")).fetchall()
        finally:
            db.close()
    except Exception:
        pytest.skip("无 DB 会话，无法校准（本用例依赖真实模板）")
    ph = set()
    for (t,) in rows:
        ph |= set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", t or ""))
    dyn = ("kline", "indicator", "flow", "market_data", "trigger", "oi_", "funding")
    static_ph = {p for p in ph if not p.startswith(dyn)}
    gap = only_prev & static_ph
    assert "news_section" in gap, "news_section 必须仍在缺口内（否则 §27 结论要更新）"
    assert len(gap) <= 10, (
        f"校准缺口升到 {len(gap)} 个（当前记录 6 个）⇒ 实盘又少了变量，请复核 §27")
    assert len(only_prev) > 3 * len(gap), (
        "AST 口径与模板口径应当差距显著；若两者接近，说明有人已把缺口补齐，请更新 §27")


def test_required_placeholders_check_is_on_the_wrong_side():
    """校验只管"占位符在不在模板里"，不管"上下文给不给"；**且注释与代码不符**。

    实测源码：
        # Non-legacy template: validate declared placeholders exist in context   ← 注释这么说
        _missing = [ph for ph in _required if "{" + ph + "}" not in _tpl_text]    ← 代码查的是模板
    ⇒ 注释让读者以为"会校验上下文"，实际不会。这是"校验查错一侧"的精确证据。
    """
    src = ADS.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"_missing\s*=\s*\[ph for ph in _required if (.+?)\]", src)
    assert m, "未找到 required_placeholders 的校验表达式"
    expr = m.group(1)
    assert "_tpl_text" in expr, f"校验表达式应针对模板文本，实测: {expr}"
    assert "context" not in expr, f"校验仍未查上下文（若已修，请同步报告 §27）: {expr}"
    # 注释确实声称查 context —— 记录下来，避免下次有人被注释误导
    i = src.rfind("validate declared placeholders", 0, m.start())
    assert i > 0 and "exist in context" in src[i:m.start() + 10], \
        "注释已改（若同时修了校验，请同步报告 §27）"
