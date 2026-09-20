# -*- coding: utf-8 -*-
"""轮125 补2：VALID_KINDS 定义在**测试**里（画布契约表），把新 kind 登记进去。"""
import io

p = "backend/tests/unit/test_agent_wall_20260919.py"
s = io.open(p, encoding="utf-8", errors="surrogateescape", newline="").read()
old = 'VALID_KINDS = {"file", "json_latest", "json_thesis", "none"}'
new = ('# [轮125 2026-09-19] 新增 `json_file`：读**运行产物 JSON**（E1 卡用它 ——\n'
       '# 那个节点每天 08:20 才跑一次，日志里非运行时段本来就没有业务行，\n'
       '# 此前用日志过滤只能捞到卡片自己的轮询访问日志）。\n'
       'VALID_KINDS = {"file", "json_latest", "json_thesis", "json_file", "none"}')
if old in s:
    s = s.replace(old, new, 1)
    io.open(p, "w", encoding="utf-8", errors="surrogateescape", newline="").write(s)
    print("[OK] 测试契约已登记 json_file")
else:
    print("[!] 未匹配")
