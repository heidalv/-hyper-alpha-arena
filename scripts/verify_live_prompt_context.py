# -*- coding: utf-8 -*-
"""[F370 运行时核验] 实盘 `PromptContextBuilder` 到底提供了哪些变量？

前面用 AST/grep 得出"`news_section`（22 个模板都在用）等变量实盘不提供"，
但静态证据可能低估（键可能由别的写法写入）。本脚本**直接调用**该构造器，
把真实产出的键集打印出来，与模板占位符求差 —— 这是最终判据。
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def main() -> int:
    from backend.services.prompt_context import PromptContextBuilder, BuildInput

    # 最小可用输入：account 用哑对象（构造器只读 name/model/leverage 等属性）
    class _Acct:
        id = 1
        name = "probe"
        model = "probe-model"
        leverage = 3
        max_leverage = 3
        initial_capital = 10000.0
        current_capital = 10000.0
        environment = "mainnet"

    print("=" * 92)
    print("运行时核验：PromptContextBuilder().build(...) 的真实产出键")
    print("=" * 92)
    ctx = None
    err = None
    db = None
    try:
        from sqlalchemy import text
        from backend.database.connection import SessionLocal
        from backend.database.models import Account
        db = SessionLocal()
        try:
            db.execute(text("SET app.is_admin='on'"))
            acct = db.query(Account).first()
        except Exception:
            acct = None
        if acct is None:
            class _Acct:
                id = 1
                name = "probe"
                model = "probe-model"
                leverage = 3
                initial_capital = 10000.0
                current_capital = 10000.0
                environment = "mainnet"
            acct = _Acct()
        from backend.services.ai_decision_service import SUPPORTED_SYMBOLS
        ctx = PromptContextBuilder().build(BuildInput(
            account=acct, db=db, portfolio={}, prices={},
            symbol_metadata=SUPPORTED_SYMBOLS,
            symbol_order=list(SUPPORTED_SYMBOLS.keys()),
        ))
    except Exception as exc:
        err = f"{type(exc).__name__}: {str(exc)[:200]}"
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
    if ctx is None:
        print(f"  构造失败（未能运行时核验）: {err}")
        print("  ⇒ 本轮只能用静态证据；结论按静态口径标注")
    else:
        keys = set(ctx.keys()) if isinstance(ctx, dict) else set(getattr(ctx, "__dict__", {}) or {})
        if not keys and hasattr(ctx, "variables"):
            keys = set(ctx.variables.keys())
        print(f"  产出 {len(keys)} 个键（{type(ctx).__name__}）")
        probe = ["news_section", "factor_guidance", "recent_trades_summary",
                 "selected_symbols_count", "decision_chain", "market_regime",
                 "strategy_wisdom", "environment"]
        for k in probe:
            print(f"    {k:<26}{'提供' if k in keys else '**不提供 ⇒ 渲染 N/A**'}")

    # 与真实模板求差
    print("\n" + "=" * 92)
    print("与 DB 模板占位符求差（这是实盘真正会渲染成 N/A 的位置）")
    print("=" * 92)
    try:
        from sqlalchemy import text
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text("SET app.is_admin='on'"))
            rows = db.execute(text(
                "SELECT key, template_text FROM prompt_templates ORDER BY id")).fetchall()
        finally:
            db.close()
    except Exception as exc:
        print("  模板读取失败:", str(exc)[:140])
        return 0

    static_keys = None
    if ctx is not None:
        static_keys = set(ctx.keys()) if isinstance(ctx, dict) else set(getattr(ctx, "__dict__", {}) or {})
        if not static_keys and hasattr(ctx, "variables"):
            static_keys = set(ctx.variables.keys())

    dyn = ("kline", "indicator", "flow", "market_data", "trigger", "oi_", "funding")
    from collections import Counter
    miss_counter: Counter = Counter()
    n_ph_total = 0
    for key, txt in rows:
        ph = {p for p in re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", txt or "")
              if not p.startswith(dyn)}
        n_ph_total += len(ph)
        if static_keys is None:
            continue
        for p in sorted(ph - static_keys):
            miss_counter[p] += 1
    print(f"  模板数={len(rows)}  静态占位符合计={n_ph_total}")
    if static_keys is None:
        print("  ⚠️ 未拿到运行时键集 ⇒ 无法求差（见上方构造失败原因）")
    else:
        print(f"  实盘**不提供**的静态占位符（按出现在多少个模板里排序）：")
        for p, n in miss_counter.most_common(30):
            print(f"    {p:<30}出现在 {n}/{len(rows)} 个模板")
        if not miss_counter:
            print("    （无）")
    print("\n  意义：这些位置在实盘 prompt 里会被 `SafeDict.__missing__` 静默填成 \"N/A\"，")
    print("        而预览路径（_build_prompt_context）会给出真实内容 ⇒ 预览不能代表实盘。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
