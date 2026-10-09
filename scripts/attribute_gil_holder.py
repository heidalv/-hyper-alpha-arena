# -*- coding: utf-8 -*-
"""精确定位「谁在烧 GIL」：OS 级每线程 CPU 增量（不受 GIL 影响）+ py-spy 栈映射。

py-spy 自带的采样器在本进程里会被饿死（实测落后 7s、91 errors），
所以用 psutil 直接读内核给出的每线程 CPU 时间，再按 TID 去 py-spy dump 里取栈。
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import psutil

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
SPY = ROOT / ".venv" / "Scripts" / "py-spy.exe"
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0


def pick_backend() -> psutil.Process:
    cands = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cl = " ".join(p.info["cmdline"] or [])
            if "run_uvicorn_dev" in cl and p.info["name"] == "python.exe":
                cands.append(p)
        except Exception:
            continue
    if not cands:
        raise SystemExit("未找到后端进程")
    cands.sort(key=lambda p: p.num_threads(), reverse=True)
    return cands[0]


def thread_cpu(proc) -> dict[int, float]:
    out = {}
    for t in proc.threads():
        out[t.id] = (t.user_time or 0.0) + (t.system_time or 0.0)
    return out


proc = pick_backend()
print(f"后端 pid={proc.pid}  线程数={proc.num_threads()}  采样 {DURATION:.0f}s …\n")

a = thread_cpu(proc)
t0 = time.perf_counter()
time.sleep(DURATION)
span = time.perf_counter() - t0
b = thread_cpu(proc)

delta = {tid: b.get(tid, 0.0) - a.get(tid, 0.0) for tid in set(a) | set(b)}
total = sum(max(0.0, v) for v in delta.values())
print(f"窗口 {span:.1f}s 内全部线程 CPU 合计 = {total:.2f}s "
      f"⇒ 等效 {total/span:.2f} 个核")
print(f"（其中「正在运行」的线程即 GIL 竞争者）\n")

top = sorted(delta.items(), key=lambda kv: -kv[1])[:14]
print("=" * 92)
print("【1】CPU 消耗最高的线程（内核读数，精确）")
print("=" * 92)
print(f"  {'TID':>8s} {'CPU_s':>8s} {'占窗口':>7s}")
for tid, cpu in top:
    print(f"  {tid:8d} {cpu:8.2f} {cpu/span*100:6.0f}%")

# ── 用 py-spy dump 取这些 TID 的栈 ──
print("\n运行 py-spy dump（98 线程，需数秒）…")
r = subprocess.run([str(SPY), "dump", "--pid", str(proc.pid), "--nonblocking"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
dump = r.stdout or ""
(ROOT / "logs" / "spy_dump_attrib.txt").write_text(dump, encoding="utf-8")

blocks = re.split(r"\n(?=Thread \d+)", dump)
stacks: dict[int, tuple[str, str]] = {}
for blk in blocks:
    m = re.match(r"Thread (\d+) \(([^)]*)\): \"([^\"]*)\"", blk)
    if not m:
        continue
    tid = int(m.group(1))
    state, name = m.group(2), m.group(3)
    frames = []
    for ln in blk.splitlines()[1:]:
        if ln.startswith("    "):
            frames.append(ln.strip())
        elif ln.strip() == "":
            continue
        else:
            break
    stacks[tid] = (f"{name} [{state}]", "\n      ".join(frames[:12]))

# 线程状态统计
st = Counter(s.split("[")[1].rstrip("]") for s, _ in stacks.values())
print(f"\n线程状态分布（{len(stacks)} 个线程）: {dict(st)}")

print()
print("=" * 92)
print("【2】CPU 最高线程的 Python 栈（栈顶=正在执行）")
print("=" * 92)
for tid, cpu in top:
    if cpu < 0.05:
        continue
    label, stack = stacks.get(tid, ("<该 TID 未出现在 dump 中：可能已结束或为新线程>", ""))
    print(f"\n── TID {tid}  CPU={cpu:.2f}s ({cpu/span*100:.0f}%)  {label}")
    for line in stack.splitlines():
        print(f"      {line.strip()}")

# ── 把 CPU 归因到栈里最深的 backend 帧 ──
print()
print("=" * 92)
print("【3】CPU 归因到业务入口（按 CPU 秒数加权）")
print("=" * 92)
biz: Counter = Counter()
for tid, cpu in delta.items():
    if cpu <= 0.05 or tid not in stacks:
        continue
    label, stack = stacks[tid]
    hit = None
    for line in stack.splitlines():
        m = re.search(r"(backend[\\/][\w\\/\.]+\.py:\d+)", line)
        if m:
            hit = m.group(1).replace("\\", "/")
    if hit:
        biz[(hit, label.split(" [")[0])] += cpu
    else:
        biz[(f"(无 backend 帧) {label.split(' [')[0]}", "")] += cpu
for (loc, tname), cpu in biz.most_common(20):
    print(f"  {cpu:7.2f}s ({cpu/span*100:5.0f}%)  {tname[:34]:36s} {loc[:60]}")
print(f"\n  合计可归因 {sum(biz.values()):.2f}s / 总 {total:.2f}s")
