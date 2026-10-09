# -*- coding: utf-8 -*-
"""聚合 py-spy speedscope 采样：谁在真正烧 CPU（self time），以及按线程归因。

用法: python scripts/aggregate_pyspy_profile.py logs/spy_backend.json
"""
from __future__ import annotations

import io
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "logs/spy_backend.json")
    data = json.loads(path.read_text(encoding="utf-8"))

    shared = data.get("shared", {}).get("frames", [])
    profiles = data.get("profiles", [])
    print(f"采样文件: {path}  profile 数={len(profiles)}  共享帧={len(shared)}\n")

    self_by_func: Counter = Counter()
    self_by_func_file: Counter = Counter()
    total_samples = 0

    for prof in profiles:
        frames = shared + prof.get("frames", [])
        weights = prof.get("weights") or [1] * len(prof.get("samples", []))
        for samp, w in zip(prof.get("samples", []), weights):
            if not samp:
                continue
            total_samples += w
            leaf = frames[samp[-1]]
            name = leaf.get("name", "?")
            self_by_func[name] += w
            self_by_func_file[(name, (leaf.get("file") or "").split("\\")[-1].split("/")[-1])] += w

    print(f"总样本权重 = {total_samples:.0f}（≈ 采样秒数 × rate）\n")

    print("=" * 92)
    print("【1】最热函数（self time，按比例）—— 谁在占用 GIL/CPU")
    print("=" * 92)
    print(f"  {'占比':>7s} {'样本':>7s}  函数")
    for name, w in self_by_func.most_common(25):
        print(f"  {w/max(1,total_samples)*100:6.1f}% {w:7.0f}  {name[:64]}")

    print()
    print("=" * 92)
    print("【2】最热「函数@文件」组合")
    print("=" * 92)
    for (name, f), w in self_by_func_file.most_common(25):
        print(f"  {w/max(1,total_samples)*100:6.1f}% {w:7.0f}  {name[:52]:54s} {f}")

    # ── 按叶子帧所属模块归类 ──
    print()
    print("=" * 92)
    print("【3】按模块归因（文件名分组）")
    print("=" * 92)
    by_mod: Counter = Counter()
    for (name, f), w in self_by_func_file.items():
        by_mod[f or "(builtin)"] += w
    for f, w in by_mod.most_common(20):
        print(f"  {w/max(1,total_samples)*100:6.1f}% {w:7.0f}  {f}")

    # ── 归因到顶层业务调用（栈里最深的 backend 帧） ──
    print()
    print("=" * 92)
    print("【4】按「最深 backend/*.py 帧」归因（业务入口）")
    print("=" * 92)
    biz: Counter = Counter()
    for prof in profiles:
        frames = shared + prof.get("frames", [])
        weights = prof.get("weights") or [1] * len(prof.get("samples", []))
        for samp, w in zip(prof.get("samples", []), weights):
            hit = None
            for idx in samp:
                fl = frames[idx]
                f = (fl.get("file") or "").replace("\\", "/")
                if "/backend/" in f and fl.get("name") != "<module>":
                    hit = (fl.get("name"), f.split("/backend/")[-1])
            if hit:
                biz[hit] += w
    for (name, f), w in biz.most_common(25):
        print(f"  {w/max(1,total_samples)*100:6.1f}% {w:7.0f}  {name[:44]:46s} backend/{f}")
