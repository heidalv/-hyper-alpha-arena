# -*- coding: utf-8 -*-
"""[F370 2026-09-18] 提示词**占位符 ↔ 上下文提供者**一致性审计（只读）。

## 为什么查这个

实盘决策链（`ai_decision_service.py` 约 :2282-2351）是这样的：

    context = PromptContextBuilder().build(BuildInput(...))
    prompt  = _tpl_text.format_map(SafeDict(context))     # SafeDict.__missing__ → "N/A"

而 `SafeDict.__missing__` 返回 `"N/A"` ⇒ **模板里任何没被上下文提供的占位符，
都会静默变成 "N/A"，不报错、不打日志**。更关键的是：模板的
`required_placeholders` 校验（:2339-2345）只检查"这些占位符**在模板文本里**存不存在"，
**从不检查"上下文是否真的提供了它们"** —— 校验查的是错的一侧。

与此同时，**预览**走的是另一套构造器 `_build_prompt_context`（:628，注释自称
"the SINGLE and ONLY function responsible for building prompt context"，
而实盘用的是"从它拆出来的" `PromptContextBuilder`）⇒ 两套上下文**可能提供不同的变量集**，
于是"预览看到的 prompt"与"实盘真正发出去的 prompt"可以不一致 —— 而预览正是用户
用来判断"AI 到底看到了什么"的窗口。

## 本脚本做什么（离线、只读）

1. 从 DB 读**真实模板**（`prompt_templates`），提取全部 `{placeholder}`；
2. AST 扫**实盘**提供者 `prompt_context/`（builder 写入的键）；
3. AST 扫**预览**提供者 `_build_prompt_context`（`dict` 字面量键 + `context["k"]=…` 赋值）；
4. 输出三类差异：
   - 模板有、实盘无 → **实盘渲染 N/A**（静默丢变量）
   - 模板有、实盘有、预览无（或反之）→ **预览 ≠ 实盘**（可观测性谎言）
   - 两边都有 → 一致
"""
from __future__ import annotations

import ast
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

#: 运行时才填的占位符（由 K 线/指标/触发上下文动态生成，AST 扫不到）——单列，不算差异
DYNAMIC_PREFIXES = ("kline", "indicator", "flow", "market_data", "trigger", "oi_", "funding")


def keys_of_module(path: Path) -> set:
    """AST：模块内所有 `x["key"] = …` 与 dict 字面量的字符串键。"""
    src = path.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")
    tree = ast.parse(src)
    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            sl = node.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                keys.add(sl.value)
        elif isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
        # 返回字典时的 **{...} / dict(a=1) 形式
        elif isinstance(node, ast.Call):
            f = node.func
            fn = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
            if fn == "dict":
                for kw in node.keywords or []:
                    if kw.arg:
                        keys.add(kw.arg)
    return keys


def template_rows():
    """读 DB 里的模板（只读）。失败返回 []。"""
    try:
        from sqlalchemy import text
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text("SET app.is_admin='on'"))
            rows = db.execute(text(
                "SELECT id, key, is_legacy, required_placeholders, template_text "
                "FROM prompt_templates ORDER BY id")).fetchall()
            return rows
        finally:
            db.close()
    except Exception as exc:
        print(f"  [DB] 模板读取失败: {str(exc)[:140]}")
        return []


def main() -> int:
    print("=" * 96)
    print("提示词占位符 ↔ 上下文提供者 一致性审计（只读）")
    print("=" * 96)

    # 实盘提供者：prompt_context 包
    live_keys: set = set()
    pkg = ROOT / "backend/services/prompt_context"
    for f in sorted(pkg.glob("*.py")):
        try:
            live_keys |= keys_of_module(f)
        except Exception as exc:
            print(f"  [AST] {f.name} 失败: {str(exc)[:80]}")
    print(f"\n① 实盘构造器 PromptContextBuilder 提供的变量键：{len(live_keys)}")

    # 预览提供者：_build_prompt_context
    ads = ROOT / "backend/services/ai_decision_service.py"
    src = ads.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")
    tree = ast.parse(src)
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_build_prompt_context":
            fn = node
            break
    prev_keys: set = set()
    if fn is not None:
        seg = ast.get_source_segment(src, fn) or ""
        for node in ast.walk(ast.parse(seg)):
            if isinstance(node, ast.Dict):
                for k in node.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        prev_keys.add(k.value)
            elif isinstance(node, ast.Subscript):
                sl = node.slice
                if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                    prev_keys.add(sl.value)
    print(f"② 预览构造器 _build_prompt_context 提供的变量键：{len(prev_keys)}")

    both = live_keys & prev_keys
    print(f"   两者共有 {len(both)}；仅实盘 {len(live_keys - prev_keys)}；仅预览 {len(prev_keys - live_keys)}")
    only_prev = sorted(prev_keys - live_keys)
    if only_prev:
        print(f"   仅预览提供（⇒ 这些变量在实盘渲染成 N/A，而预览有值）：{only_prev[:25]}")

    rows = template_rows()
    print(f"\n③ 模板：{len(rows)} 个")
    total_missing_live = []
    for tid, key, is_legacy, req, text in rows:
        ph = set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", text or ""))
        dyn = {p for p in ph if p.startswith(DYNAMIC_PREFIXES)}
        static = ph - dyn
        miss_live = sorted(static - live_keys)
        miss_prev = sorted(static - prev_keys)
        inconsistent = sorted((static & live_keys) ^ (static & prev_keys))
        print(f"\n   #{tid} {key}  (is_legacy={is_legacy}, 占位符 {len(ph)}，其中动态 {len(dyn)})")
        print(f"      实盘缺: {miss_live if miss_live else '无'}")
        print(f"      预览缺: {miss_prev if miss_prev else '无'}")
        print(f"      两套不一致: {inconsistent if inconsistent else '无'}")
        if miss_live:
            total_missing_live.append((key, miss_live))
        if req:
            reqs = req if isinstance(req, list) else re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", str(req))
            bad = [p for p in reqs if p not in live_keys]
            print(f"      required_placeholders 声明 {len(reqs)} 个，其中实盘未提供 {len(bad)}: {bad[:12]}")

    print("\n" + "=" * 96)
    print("结论")
    print("=" * 96)
    if total_missing_live:
        print("  ⚠️ 存在『模板要、实盘不给』的静态占位符 ⇒ 这些位置在**实盘 prompt 里恒为 N/A**")
        for k, v in total_missing_live:
            print(f"     {k}: {v[:15]}")
    else:
        print("  ✅ 所有模板的静态占位符都被实盘构造器提供（无静默 N/A）")
    if only_prev:
        print("  ⚠️ 预览比实盘多提供的变量（预览≠实盘）:")
        for k in only_prev[:20]:
            print(f"     {k}")
    print("\n  机制提醒：渲染用 `format_map(SafeDict(context))`，缺键 → 'N/A' 且**无日志**；")
    print("           `required_placeholders` 只校验『占位符在模板里』，不校验『上下文提供了没』。")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
