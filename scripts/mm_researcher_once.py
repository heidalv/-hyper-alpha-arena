# -*- coding: utf-8 -*-
"""[h650] mm 车道离线研究员:单次运行(只读行情 + 问 deepseek-flash + 落研究队列)。

零权限:本脚本唯一副作用 = 追加一行到 `data/mm_researcher_queue.jsonl`。
线上报价/参数不经它改动。用法:
    python scripts/mm_researcher_once.py [--hours 4]
建议由计划任务每 4 小时跑一次(预算 ≤6 次/天,llm2 记账另配)。
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    hours = 4.0
    if "--hours" in sys.argv:
        i = sys.argv.index("--hours")
        hours = float(sys.argv[i + 1])
    from backend.services.market_maker.researcher import run_once

    entry = run_once(hours=hours)
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    if not entry.get("ok"):
        print(f"✗ 研究员本轮失败: {entry.get('error')}")
        return 1
    if not entry.get("queued"):
        print("✗ 提案未落队列(零权限:未落地=无效)")
        return 2
    print(f"✓ 已落队列 {len(entry.get('proposals') or [])} 条提案")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
