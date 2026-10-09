# -*- coding: utf-8 -*-
"""[F374 2026-09-18] F370 决策辅助：**预览独有变量逐个溯源**（只读）。

F370 的两个选项一直不好拍，因为没人说清"补齐 47 个变量"到底要动什么。
本脚本把每个"仅预览提供"的变量溯源到它的**来源类型**，并判定实盘构造器**是否已具备该输入**：

| 来源类型 | 含义 | 选项①（补齐进实盘）要做什么 |
|---|---|---|
| `caller_param` | 预览从**函数参数**拿到（调用方传的） | 需给 `BuildInput` 加字段 **+ 实盘调用方补传** ⇒ 改动最大 |
| `computed` | 预览在函数体内**现算**（调 DB / 服务） | 需把同样的取数逻辑搬进 `prompt_context` 子构造器 |
| `derived` | 由其它上下文/字段**派生** | 需在子构造器里补派生逻辑（通常最便宜） |
| `static` | 模块常量/字面量 | 直接复制即可 |

输出按"改动成本"分组，供选项①/②取舍；不改任何代码、不写库。
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

ADS = ROOT / "backend/services/ai_decision_service.py"
PC_PKG = ROOT / "backend/services/prompt_context"
DYNAMIC_PREFIXES = ("kline", "indicator", "flow", "market_data", "trigger", "oi_", "funding")


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")


def preview_fn():
    src = _src(ADS)
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_build_prompt_context"), None)
    return src, fn


def classify(node: ast.AST, params: set) -> str:
    """给"该键的取值表达式"归类。"""
    if isinstance(node, ast.Name):
        return "caller_param" if node.id in params else "derived"
    if isinstance(node, ast.Call):
        return "computed"
    if isinstance(node, (ast.JoinedStr, ast.BinOp)):
        # f-string / 拼接里是否引用了参数
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        return "caller_param" if (names & params) else "derived"
    if isinstance(node, ast.Subscript):
        return "derived"
    if isinstance(node, ast.Constant):
        return "static"
    return "derived"


def main() -> int:
    src, fn = preview_fn()
    assert fn is not None, "未找到 _build_prompt_context"
    params = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
    body = fn.body

    # 预览：键 -> 取值表达式（dict 字面量 + context[...] = ...）
    prev: dict = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    prev.setdefault(k.value, v)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) \
                        and isinstance(t.slice.value, str):
                    prev.setdefault(t.slice.value, node.value)

    # 实盘：prompt_context 包提供的键
    live: set = set()
    for f in sorted(PC_PKG.glob("*.py")):
        for node in ast.walk(ast.parse(_src(f))):
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                    and isinstance(node.slice.value, str):
                live.add(node.slice.value)
            elif isinstance(node, ast.Dict):
                for k in node.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        live.add(k.value)

    # BuildInput 字段
    bi = {n.target.id for n in ast.walk(ast.parse(_src(PC_PKG / "types.py")))
          if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}

    only_prev = sorted(k for k in prev if k not in live)

    # [F374 自我更正] 只看**真实模板占位符**：`prev` 里混着大量嵌套数据结构的 dict 键
    # （`{"15m":…, "1h":…}` 之类），它们根本不是模板变量。用 DB 模板求交才是可上报的口径。
    tpl_ph: set = set()
    tpl_n = 0
    try:
        from sqlalchemy import text
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text("SET app.is_admin='on'"))
            rows = db.execute(text("SELECT template_text FROM prompt_templates")).fetchall()
            tpl_n = len(rows)
            for (t,) in rows:
                tpl_ph |= set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", t or ""))
        finally:
            db.close()
    except Exception as exc:
        print(f"  [DB] 模板读取失败（退化为全键口径）: {str(exc)[:110]}")
    static_ph = {p for p in tpl_ph if not p.startswith(DYNAMIC_PREFIXES)}
    only_prev_ph = [k for k in only_prev if k in static_ph]

    groups: dict = {}
    for k in only_prev_ph:
        kind = classify(prev[k], params)
        groups.setdefault(kind, []).append(k)

    print("=" * 92)
    print("F370 决策辅助：仅预览提供的变量逐个溯源")
    print("=" * 92)
    print(f"  预览提供 {len(prev)} 个键 / 实盘提供 {len(live)} 个键 / 仅预览 {len(only_prev)}")
    print(f"  **真实模板 {tpl_n} 个，静态占位符 {len(static_ph)} 个**；"
          f"其中『模板在用、实盘不给』= **{len(only_prev_ph)}** ← 这才是会渲染成 N/A 的口径")
    print(f"  BuildInput 输入字段 {len(bi)} 个；预览函数参数 {len(params)} 个")
    dropped = len(only_prev) - len(only_prev_ph)
    if dropped:
        print(f"  （另有 {dropped} 个『仅预览』键**不是模板占位符**——多为嵌套数据结构的 dict 键，"
              f"如 15m/1h/4h；报数时必须剔除，否则会虚高）")

    order = ["static", "derived", "computed", "caller_param"]
    label = {"static": "静态字面量（最便宜）", "derived": "由其它字段派生",
             "computed": "函数体内现算（取数/服务）", "caller_param": "来自调用方参数（最贵：要改签名+调用方）"}
    for kind in order:
        ks = groups.get(kind) or []
        if not ks:
            continue
        print(f"\n① {label[kind]}：{len(ks)} 个")
        for k in ks:
            bi_hit = "BuildInput 已有同名字段" if k in bi else "BuildInput 无此字段"
            print(f"    {k:<34}{bi_hit}")
            if kind == "caller_param":
                print(f"        ⇒ 选项①需：给 BuildInput 加字段 + 实盘调用方补传该值")

    # 关键几个单独说明
    print("\n② 你大概率最关心的几个")
    for k in ("news_section", "factor_guidance", "market_regime", "decision_chain",
              "decision_performance_history", "recent_trades_summary", "selected_symbols_count"):
        if k in prev:
            kind = classify(prev[k], params)
            print(f"    {k:<32}来源={kind:<12}{'BuildInput 有' if k in bi else 'BuildInput 无'}")
        else:
            print(f"    {k:<32}（不在预览键集里）")

    print("\n" + "=" * 92)
    print("两个选项的成本对照（供拍板）")
    print("=" * 92)
    n_static = len(groups.get("static") or [])
    n_derived = len(groups.get("derived") or [])
    n_computed = len(groups.get("computed") or [])
    n_param = len(groups.get("caller_param") or [])
    print(f"  选项①（补齐进实盘）：共 {len(only_prev)} 个变量 —— "
          f"静态 {n_static} + 派生 {n_derived} + 现算 {n_computed} + 需调用方补传 {n_param}")
    print(f"      · 最便宜的 {n_static + n_derived} 个可在 `prompt_context` 子构造器内完成（不动签名）;")
    print(f"      · {n_computed} 个需把取数逻辑搬进子构造器（注意延迟：这些会在**每次决策**前跑）;")
    print(f"      · {n_param} 个需改 `BuildInput` 签名 + 实盘调用方（`_build_prompt_context` 之外）补传。")
    print(f"  选项②（预览改用同一构造器）：改动集中在 `api/prompt_routes.py` 一处，")
    print(f"      代价是**预览也会显示这 {len(only_prev)} 个 N/A**（诚实但信息更少）。")
    print("\n  ⚠️ 提醒：①会让实盘 LLM **开始看到新闻等新内容** ⇒ 属决策行为变更；②只改观测面。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
