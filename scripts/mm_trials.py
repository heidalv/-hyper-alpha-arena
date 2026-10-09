# -*- coding: utf-8 -*-
"""[F268] 试验记账（trials accounting）：把每一次参数/结构扫描登记在案。

为什么必须有（行业惯例 + 本项目痛点）：本项目已跑过 30+ 轮参数扫描，但没有一处
记录「一共试了多少组配置」⇒ 任何"某配置胜出"的结论都无法做多重检验校正，
也无法回答「这个结论是在多少次尝试后挑出来的」（选择偏差）。
本模块提供 append-only 台账 `logs/mm_trials.jsonl`：

    {ts, kind, window, delays, n_configs, configs_digest, verdict, note, script}

用法（命令行登记）：
    python scripts/mm_trials.py add --kind sweep --window "09-15 全天" \
        --delays 18200,25100,31800 --n-configs 4 --verdict "w=12 在位最优" \
        --script scripts/mm_freq_pnl_experiment.py --note "..."
    python scripts/mm_trials.py report      # 汇总：累计试验数 / 各判定分布 / 近 10 条
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(REPO, "logs", "mm_trials.jsonl")


def append(entry: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_all() -> List[Dict[str, Any]]:
    if not os.path.exists(LEDGER):
        return []
    out = []
    with open(LEDGER, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add")
    a.add_argument("--kind", default="sweep", help="sweep|experiment|ab|live_change")
    a.add_argument("--window", default="")
    a.add_argument("--delays", default="")
    a.add_argument("--n-configs", type=int, default=0)
    a.add_argument("--configs-digest", default="")
    a.add_argument("--verdict", default="")
    a.add_argument("--script", default="")
    a.add_argument("--note", default="")

    sub.add_parser("report")

    args = ap.parse_args()

    if args.cmd == "add":
        entry = {
            "ts": datetime.now(timezone.utc).astimezone().isoformat(),
            "kind": args.kind,
            "window": args.window,
            "delays": args.delays,
            "n_configs": int(args.n_configs),
            "configs_digest": args.configs_digest,
            "verdict": args.verdict,
            "script": args.script,
            "note": args.note,
        }
        # 去重指纹：同脚本+同窗口+同配置数+同判定 ⇒ 视为重复登记
        sig = hashlib.sha1(json.dumps(
            {k: entry[k] for k in ("kind", "window", "delays", "n_configs",
                                   "configs_digest", "verdict", "script")},
            sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
        entry["sig"] = sig
        if any(r.get("sig") == sig for r in read_all()):
            print(f"（重复登记，已跳过）sig={sig}")
            return 0
        append(entry)
        print(f"已登记 sig={sig}: {entry['kind']} / {entry['window']} / "
              f"{entry['n_configs']} 组 / {entry['verdict'][:40]}")
        return 0

    rows = read_all()
    print(f"台账 {LEDGER}")
    print(f"累计登记 {len(rows)} 条；累计扫描配置数 = "
          f"{sum(int(r.get('n_configs') or 0) for r in rows)}")
    kinds: Dict[str, int] = {}
    for r in rows:
        kinds[r.get("kind") or "?"] = kinds.get(r.get("kind") or "?", 0) + 1
    print(f"类型分布: {kinds}")
    print("近 10 条:")
    for r in rows[-10:]:
        print(f"  {str(r.get('ts'))[:19]} [{r.get('kind')}] {r.get('window')} "
              f"×{r.get('n_configs')} → {str(r.get('verdict'))[:50]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
