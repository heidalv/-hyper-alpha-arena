# -*- coding: utf-8 -*-
"""主脑 prompt 是否真的带上 1h 读数（第十八轮端到端验证）。

直接构建 midlong_thesis 的 ContextPack 并渲染 prompt 文本，检查
ret_24h_pct / pos24_pct / rsi14_1h 是否出现在发给 LLM 的文本里。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from backend.services.analysis.context_pack import build  # noqa: E402

pack = build("midlong_thesis", symbols=["BTC"])
text = pack.to_prompt_text(40000)
print(f"prompt 字符数: {len(text)}")
for key in ("ret_24h_pct", "pos24_pct", "range_24h_high", "rsi14_1h", "atr14_1h_pct",
            "ema_trend_1h", "ret_1d_pct", "ema_trend_4h"):
    hit = key in text
    idx = text.find(key)
    snippet = text[max(0, idx - 10): idx + 60].replace("\n", " ") if hit else ""
    print(f"  {key:<16} {'✓' if hit else '✗'}  {snippet}")
