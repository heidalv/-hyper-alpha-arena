# -*- coding: utf-8 -*-
"""[2026-10-03] ModelGateway「解析/schema 失败重试」护栏（用户指令「继续」的第 1 项）。

## 实测量化（llm_quota_usage，近 7 天）
· `mlto_debate`（大脑多空辩论，核心路径）：**n=6,433 / 失败 1,351（21%）**；
· 失败行与成功行的**延迟同构**（p50 3.9s vs 3.6s）、**Token 非零**（avg ~967 in / ~644 out）；
· 失败行 output_tokens p50=702、max=1135 —— **不是截断**（上限 2,500），
  也不是超时（>300s 的调用全库仅 26 条 / 0.25%）、不是限流（失败按小时均匀分布）。
⇒ 失败 = **"输出中无可解析 JSON" 或 "schema 校验失败"**，而 `ok` 仅由这两者决定
   （`model_gateway.py`），**且重试只在抛异常时发生** ⇒ 模型答了却没有补救。
· 顺带澄清：`deep` 档 7 天只有 7 次调用、全失败，全部是 `daily_brief` 日报，
  与中线/大脑无关（据此可排除"deep 档坏掉"的猜测）。

## 修复
`model_gateway.call`：解析/schema 失败时，**追加一次严格 JSON 纠错重试**
（temperature=0、附"只输出一个 JSON 对象"指令，token 累加、成功则覆盖判定），
失败保持原判定（不掩盖）。回滚：`MODEL_GATEWAY_JSON_RETRY=0`。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SRC = (ROOT / "backend/services/analysis/model_gateway.py").read_text(encoding="utf-8")


def test_json_retry_is_wired_and_gated():
    assert 'MODEL_GATEWAY_JSON_RETRY", "1"' in SRC, "默认开启（可回滚）"
    assert "if not res.ok and int(os.getenv(" in SRC, "只在失败时重试"
    assert "格式纠错" in SRC, "重试必须带纠错指令"
    assert "temperature=0.0" in SRC, "纠错重试应贪心解码"
    assert "JSON 纠错重试成功" in SRC


def test_json_retry_does_not_mask_failure():
    assert "if valid2:" in SRC and "res.ok = True" in SRC
    assert "保持原判定" in SRC


def test_retry_accumulates_tokens():
    """重试消耗的 token 必须累加（否则记账失真）。"""
    assert "res.input_tokens = (res.input_tokens or 0) +" in SRC
    assert "res.output_tokens = (res.output_tokens or 0) +" in SRC


def test_behavioral_verification_pending_running_session():
    """行为级验证需要真实会话在跑（辩论只在交易循环里触发）。

    当前所有会话处于 stopped，故本轮的运行时证据只有：
      · 源码级接线（上面三条）+ py_compile 通过；
      · 失败成因的量化（llm_quota_usage：失败行非截断/非超时/非限流）。
    待会话恢复后可用日志断言：`[ModelGateway] mlto_debate/... JSON 纠错重试成功`。
    """
    assert "MODEL_GATEWAY_JSON_RETRY" in SRC
