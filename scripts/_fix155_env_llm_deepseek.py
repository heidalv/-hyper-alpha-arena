# -*- coding: utf-8 -*-
"""[轮155] 把 .env 的 LLM 传输配置切成「deepseek 单模型、零 minimax」。

用户指令：
  1) 「现在llm，全部换成deepseek fl 吧 不用minimax了」
  2) 「不要用pro」
  3) 「那就去掉这个双模型验证，这个也是累赘」

改动（只动这几行，其余不碰；脚本幂等，可重复跑）：
  ANALYSIS_PRIMARY_TRANSPORTS   minimax,deepseek → deepseek
  ANALYSIS_FALLBACK_TRANSPORTS  glm_opencode_alt,deepseek,minimax → deepseek
  ANALYSIS_ARBITER_TRANSPORT    glm_opencode_alt → deepseek（单模型模式下仲裁席位不再使用）
  ANALYSIS_5H/WEEKLY/DAILY_CALLS_MAP  去掉 minimax 条目
  + 追加（若不存在）：
      ANALYSIS_SINGLE_MODEL_MODE=true        # 去掉双模型交叉验证
      ANALYSIS_SINGLE_MODEL_MIN_CONF=0.0     # 单模型下不额外设置信门槛，交给下游闸门
      DEEPSEEK_MODEL=deepseek-v4-flash       # 仅当缺失时补（该名回显 deepseek-flash）
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ENV = Path(__file__).resolve().parents[1] / ".env"
txt = ENV.read_text(encoding="utf-8")
orig = txt

REPL = [
    (r"(?m)^ANALYSIS_PRIMARY_TRANSPORTS=.*$",
     "ANALYSIS_PRIMARY_TRANSPORTS=deepseek"),
    (r"(?m)^ANALYSIS_FALLBACK_TRANSPORTS=.*$",
     "ANALYSIS_FALLBACK_TRANSPORTS=deepseek"),
    (r"(?m)^ANALYSIS_ARBITER_TRANSPORT=.*$",
     "ANALYSIS_ARBITER_TRANSPORT=deepseek"),
    (r"(?m)^ANALYSIS_5H_CALLS_MAP=.*$",
     "ANALYSIS_5H_CALLS_MAP=glm_opencode:4500,glm_opencode_alt:4500"),
    (r"(?m)^ANALYSIS_WEEKLY_CALLS_MAP=.*$",
     "ANALYSIS_WEEKLY_CALLS_MAP=glm_opencode:100000,glm_opencode_alt:100000"),
    (r"(?m)^ANALYSIS_DAILY_CALLS_MAP=.*$",
     "ANALYSIS_DAILY_CALLS_MAP=glm_opencode_alt:5000,glm_opencode:5000"),
]
for pat, new in REPL:
    before = txt
    txt = re.sub(pat, new, txt, count=1)
    print(f"   {'已改' if txt != before else '未命中'}: {new}")

ADD = (
    "\n# [轮155 2026-09-21 用户指令] LLM 全部走 deepseek-flash、去掉双模型交叉验证\n"
    "ANALYSIS_SINGLE_MODEL_MODE=true\n"
    "ANALYSIS_SINGLE_MODEL_MIN_CONF=0.0\n"
)
if "ANALYSIS_SINGLE_MODEL_MODE=" not in txt:
    txt += ADD
    print("   已追加: ANALYSIS_SINGLE_MODEL_MODE / ANALYSIS_SINGLE_MODEL_MIN_CONF")

if "DEEPSEEK_MODEL=" not in txt:
    txt += "DEEPSEEK_MODEL=deepseek-v4-flash\n"
    print("   已追加: DEEPSEEK_MODEL=deepseek-v4-flash")

if txt != orig:
    ENV.write_text(txt, encoding="utf-8")
    print(f"\n[已写入] {ENV}")
else:
    print("\n[无改动]")

print("\n改动后的相关行：")
for ln in txt.splitlines():
    if re.match(r"^(ANALYSIS_(PRIMARY|FALLBACK|ARBITER|SINGLE|5H|WEEKLY|DAILY)|DEEPSEEK_MODEL|MINIMAX_MODEL)", ln):
        print("   ", ln)
