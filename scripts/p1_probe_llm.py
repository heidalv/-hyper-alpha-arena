# -*- coding: utf-8 -*-
"""P1 前置：验证现成的 LLM 通路可用（一次最小调用）。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

print("=" * 84)
print("P1 前置：LLM 通路验证")
print("=" * 84)

try:
    from backend.services.factors_lab.common import call_llm, parse_json_block
    print("  import OK")
except Exception as e:
    print(f"  import FAIL: {type(e).__name__}: {e}")
    raise SystemExit(1)

t0 = time.time()
raw = call_llm(
    "你是量化做市系统的诊断助手。只输出 JSON，不要解释。",
    '下面是一个币的近30分钟指标。请只输出 JSON：'
    '{"diagnosis":"...","cause":"structural|market_event|execution_decay|param_mismatch",'
    '"confidence":0.0}\n'
    '币=XRP 近30min: 笔数=120 价差=+0.9bp 行情=-5.8bp 费=0bp 净=-4.9bp；'
    '全时代强平率=10%；当前持仓1.8条腿（上限2）。',
    caller="p1_llm_probe",
    max_tokens=400,
    temperature=0.2,
)
dt = time.time() - t0
print(f"  耗时 {dt:.1f}s")
print(f"  原始返回：{'(None —— 调用失败或无配置)' if raw is None else raw[:500]}")

if raw:
    obj = parse_json_block(raw)
    print(f"\n  解析结果：{obj}")
    print(f"  ⇒ {'**通路可用**' if obj else '返回了文本但 JSON 解析失败'}")
else:
    print(f"\n  ⇒ 调用失败。需检查：DEEPSEEK_API_KEY / 租户 LLM 配置 / 网络代理")
