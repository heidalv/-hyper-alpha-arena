# -*- coding: utf-8 -*-
"""[2026-10-09 进化重挂] ping-pong 参数进化运行器。

默认只出提案（写 data/pp_evolution_journal.jsonl，不改参数）。
`--apply` 且 `MM_AUTO_EVOLVE=1` 时把桶门阈值写回 data/flow_learn_params.json
（worker 下一拍通过 runner 透传热采用，无需重启）。

用法：python scripts/pp_evolution.py [--apply]
"""
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from backend.services.market_maker.evolution import pp_evolution_round  # noqa: E402


def main() -> int:
    apply = "--apply" in sys.argv
    r = pp_evolution_round(apply=apply)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
