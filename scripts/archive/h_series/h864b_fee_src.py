# -*- coding: utf-8 -*-
"""[h864b] 读 _record_fills 里 fee 的写法(为什么挂单离场腿被收了 4bp)。"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
src = open(r"D:\001Alpha\Hyper-Alpha-Arena\backend\services\market_maker\runner.py",
           encoding="utf-8").read().splitlines()
start = next(i for i, l in enumerate(src) if l.strip().startswith("def _record_fills"))
# 找 fee 相关行
for i in range(start, min(start + 120, len(src))):
    l = src[i]
    if any(k in l for k in ("fee_bp", "fee_usd", "TAKER_FEE", "is_flatten", "record_fill",
                            "exit_path", "flatten")):
        print(f"{i+1}: {l[:120]}")
