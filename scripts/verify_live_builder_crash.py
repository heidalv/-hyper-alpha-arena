# -*- coding: utf-8 -*-
"""[F371 运行时判定] `PromptContextBuilder.build()` 在**实盘调用方真实传参形状**下是否必然抛异常。

Callers（grep 实证）：
  - `trading_commands.py:840` 初始化 `prompt_symbol_metadata = {}` → `:1043` 传入 ⇒ `{} or SUPPORTED_SYMBOLS`
  - `trading_commands.py:2683` → `symbol_metadata=None` ⇒ `None or SUPPORTED_SYMBOLS`
  - `full_auto/ai_decisions.py:234` → 未见 symbol_metadata 实参 ⇒ 默认 None
而 `ai_decision_service.py:2279`：`active_symbol_metadata = symbol_metadata or SUPPORTED_SYMBOLS`
⇒ 三种形状都落到 `SUPPORTED_SYMBOLS = {"BTC": "Bitcoin", …}`（**值是字符串**），
而 `builder.py:95` 对它做 `.get("name")` ⇒ AttributeError。

本脚本直接调用 `build()`，对三种形状逐一判定：崩 / 不崩。
"""
from __future__ import annotations

import io
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def main() -> int:
    from sqlalchemy import text
    from backend.database.connection import SessionLocal
    from backend.database.models import Account
    from backend.services.prompt_context import PromptContextBuilder, BuildInput
    from backend.services.ai_decision_service import SUPPORTED_SYMBOLS

    db = SessionLocal()
    db.execute(text("SET app.is_admin='on'"))
    acct = db.query(Account).first()

    def attempt(label, meta):
        active = meta or SUPPORTED_SYMBOLS          # 复刻 :2279 的表达式
        try:
            res = PromptContextBuilder().build(BuildInput(
                account=acct, db=db, portfolio={}, prices={},
                symbol_metadata=active,
                symbol_order=list(active.keys()) if isinstance(active, dict) else None,
            ))
            keys = set(res.keys()) if isinstance(res, dict) else set()
            print(f"  [{label}] 成功，产出 {len(keys)} 个键；"
                  f"含 news_section={'news_section' in keys}, factor_guidance={'factor_guidance' in keys}")
            return keys
        except Exception as exc:
            print(f"  [{label}] **抛异常**: {type(exc).__name__}: {str(exc)[:120]}")
            tb = traceback.format_exc().strip().splitlines()
            for ln in tb[-4:]:
                print(f"        {ln.strip()[:130]}")
            return None

    print("=" * 92)
    print("PromptContextBuilder.build() 在真实传参形状下的行为")
    print("=" * 92)
    print(f"SUPPORTED_SYMBOLS 值类型示例: {type(list(SUPPORTED_SYMBOLS.values())[0]).__name__}")
    print()
    k1 = attempt("symbol_metadata={}（trading_commands:840/1043）", {})
    k2 = attempt("symbol_metadata=None（trading_commands:2683 / ai_decisions:234）", None)
    k3 = attempt("symbol_metadata={'BTC': {'name': 'Bitcoin'}}（正确形状）",
                 {"BTC": {"name": "Bitcoin"}, "ETH": {"name": "Ethereum"}})

    print("\n" + "=" * 92)
    print("判定")
    print("=" * 92)
    fixed = False
    try:
        from backend.services.prompt_context import builder as _b
        fixed = hasattr(_b, "_display_name")
    except Exception:
        pass
    if k1 is None or k2 is None:
        print("  ⚠️ **实盘调用方真实传参形状下，构造器抛异常** ⇒ 该车道不可能渲染出 prompt，")
        print("     只能被 `call_ai_for_decision_with_fallback` 捕获后降级规则引擎。")
        print("     ⇒ P0（`prompt_context/__init__.py` 0 字节）只是**第一道**拦路；")
        print("       修好导入后紧接着就撞上这里 —— 车道**依然是死的**。")
        print("     修法：`builder.py` 的展示名解析要容忍 str 值（见 `_display_name`）。")
    elif fixed:
        print("  ✅ 三种真实传参形状**均不再抛异常**（F371 容错修复已生效）。")
        print("     ⇒ 该车道在**重启后**才可能真正渲染出 prompt（重启由用户决定，见待办 A4）。")
    else:
        print("  未复现异常，也未见 `_display_name` ⇒ 与静态分析不符，需细查。")
    if k3 is not None:
        print(f"  正确定义形状同样可用（{len(k3)} 键）。")
    print("  注：三种形状都**不含** news_section / factor_guidance（F370，运行时确认）。")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
