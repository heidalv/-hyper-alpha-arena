#!/usr/bin/env python3
"""ReplayHarness CLI。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
except Exception:
    pass


def main() -> int:
    p = argparse.ArgumentParser(description="ReplayHarness — 同管道 evaluate 回测")
    p.add_argument("--symbol", default="BTC", help="单标的（未用 --symbols 时生效）")
    p.add_argument("--symbols", default="", help="逗号分隔多标的，配合 --all-tiers 做全覆盖")
    p.add_argument("--tier", default="mid", choices=["short", "mid", "long"])
    p.add_argument("--all-tiers", action="store_true", help="覆盖 short/mid/long 三个 tier")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    from backend.services.replay.replay_harness import replay_harness

    # 批量 / 全覆盖模式
    if args.all_tiers or args.symbols:
        syms = [s.strip().upper() for s in (args.symbols or args.symbol).split(",") if s.strip()]
        tiers = ["short", "mid", "long"] if args.all_tiers else [args.tier.lower()]
        batch = replay_harness.run_batch(syms, tiers)
        data = batch.to_dict()
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(f"Replay batch: {syms} × {tiers}")
            print(f"  proposals={data['total_proposals']} allow={data['total_allowed']} "
                  f"block={data['total_blocked']} allow_rate={data['allow_rate']}")
            print(f"  per_tier: {data['per_tier']}")
            print(f"  block_reasons: {data['block_reasons']}")
        return 0

    report = replay_harness.run(symbol=args.symbol.upper(), tier=args.tier.lower())
    data = report.to_dict()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"Replay {args.symbol} tier={args.tier}")
        for k, v in data.items():
            print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
