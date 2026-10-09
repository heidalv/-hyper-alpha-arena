# -*- coding: utf-8 -*-
"""[目标·数据链路] 直接渲染提示词块：K 线块 / 深度K线 / 因子块到底有没有内容。

用户问题：① 因子、学习、数据的传输对不对 ② 提示词里有没有正确接入 K 线数据
        ③ 分析准确率为什么低。

第一步先回答②：把生产函数**真的调用一遍**，看返回文本是否为空、是否过期、口径是否对。
只读（不改任何生产状态）。
"""
from __future__ import annotations

import io
import sys
import traceback

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(".env", override=False)

SYMS = ["BTC", "ETH", "SOL"]


def show(title: str, fn, *a, **kw) -> None:
    print("\n" + "=" * 100)
    print("### %s" % title)
    try:
        out = fn(*a, **kw)
    except Exception:
        print("  ✗ 调用抛异常：")
        traceback.print_exc(limit=4)
        return
    if isinstance(out, tuple):
        txt = out[0] if out else ""
        errs = out[1] if len(out) > 1 else None
        print("  返回类型=tuple；errs=%r" % (errs,))
    else:
        txt = out
    s = "" if txt is None else str(txt)
    print("  长度=%d 字符 ｜ 行数=%d" % (len(s), s.count("\n") + 1))
    if not s.strip():
        print("  ✗✗ **空内容**（提示词里等于没有这一段）")
        return
    lines = s.splitlines()
    print("  ── 前 20 行 ──")
    for ln in lines[:20]:
        print("  | " + ln[:170])
    if len(lines) > 20:
        print("  …（共 %d 行，后 5 行）" % len(lines))
        for ln in lines[-5:]:
            print("  | " + ln[:170])


def main() -> int:
    # 1) agent_deep_context.build_kline_block
    from backend.services.agent_deep_context import build_kline_block

    for sym in SYMS[:2]:
        for periods in (["4h", "1d"], ["1h", "4h", "1d", "1w"]):
            show("build_kline_block(%s, %s)" % (sym, periods), build_kline_block, sym, periods)

    # 2) analysis.chart_service.deep_kline_text
    try:
        from backend.services.analysis.chart_service import deep_kline_text

        for sym in SYMS[:2]:
            show("deep_kline_text(%s, ('1w','1d','4h'))" % sym, deep_kline_text, sym, ("1w", "1d", "4h"))
    except Exception as e:
        print("\n[deep_kline_text 导入失败] %s" % e)

    # 3) trading_analysts._build_factor_signals_prompt_block
    try:
        from backend.services import trading_analysts as TA

        inst = None
        for name in dir(TA):
            obj = getattr(TA, name)
            if isinstance(obj, type) and hasattr(obj, "_build_factor_signals_prompt_block"):
                inst = obj
                break
        print("\n" + "=" * 100)
        print("### 找到因子块所在类：%s" % getattr(inst, "__name__", None))
        if inst is not None:
            try:
                obj = inst.__new__(inst)          # 不跑 __init__，避免副作用
            except Exception:
                obj = inst
            show("_build_factor_signals_prompt_block({}) 空参", obj._build_factor_signals_prompt_block, {})
    except Exception as e:
        print("\n[因子块定位失败] %s" % e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
