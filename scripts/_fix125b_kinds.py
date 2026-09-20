# -*- coding: utf-8 -*-
"""轮125 补：把 json_file 加进 VALID_KINDS（画布测试会校验 kind 合法性）。"""
import io

p = "backend/services/agent_wall.py"
s = io.open(p, encoding="utf-8", errors="surrogateescape", newline="").read()
old = 'VALID_KINDS = {"file", "json_latest", "json_thesis", "none"}'
new = ('VALID_KINDS = {"file", "json_latest", "json_thesis", "json_file", "none"}'
       '  # [轮125] json_file = 读运行产物 JSON（E1 卡用它：日志里非运行时段没有业务行）')
if old in s:
    s = s.replace(old, new, 1)
    io.open(p, "w", encoding="utf-8", errors="surrogateescape", newline="").write(s)
    print("[OK] VALID_KINDS 已加 json_file")
else:
    for ln in s.split("\n"):
        if "VALID_KINDS" in ln:
            print("现状:", ln.strip()[:120])
