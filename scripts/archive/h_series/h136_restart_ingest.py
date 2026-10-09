# -*- coding: utf-8 -*-
"""[H136 2026-09-21] 重启 Aster 采集，让新符号名单生效（USD1 + 补齐深度缺口）。

# 为什么用这个脚本而不是手敲命令

采集进程从 `D:\001Alpha\research_l1` 启动，命令行很长（35 个 book 符号 + 28 个深度符号）。
手敲极易漏符号（看护脚本头部记录过两次真实事故：名单不一致导致 6 分钟 / 32 分钟停采）。
本脚本**从看护脚本读取权威名单**（它是单一权威源，已由 `sync_watchdog_symbols.py` 同步），
拼出与看护脚本**逐字一致**的命令行，再重启。

# 安全点

  · 先校验：新名单必须 ⊇ 旧名单（不能因为重启而丢符号）
  · 重启前打印 old/new 差集，留审计痕迹
  · 用 `subprocess.Popen` 脱离父进程，避免随本脚本退出而终止

用法：
    .venv\\Scripts\\python.exe scripts\\h136_restart_ingest.py --dry-run
    .venv\\Scripts\\python.exe scripts\\h136_restart_ingest.py --apply
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WATCHDOG = ROOT / "scripts" / "aster-depth-watchdog.ps1"
RESEARCH = Path(r"D:\001Alpha\research_l1")
PYEXE = ROOT / ".venv" / "Scripts" / "python.exe"
RE_SYMBOLS = re.compile(r"\[string\]\$Symbols = '([^']*)',")
RE_DEPTH = re.compile(r"\[string\]\$DepthSymbols = '([^']*)',")


def authoritative() -> tuple:
    src = WATCHDOG.read_text(encoding="utf-8-sig")
    m1, m2 = RE_SYMBOLS.search(src), RE_DEPTH.search(src)
    if not m1 or not m2:
        raise SystemExit("ERR 无法从看护脚本解析权威名单")
    syms = [s for s in m1.group(1).split(",") if s]
    dep = [s for s in m2.group(1).split(",") if s]
    return syms, dep


def live_lists() -> tuple:
    """当前在跑的采集进程用的名单（从命令行解析）。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -like '*aster_ws_ingest*' } | "
             "Select-Object -ExpandProperty CommandLine"],
            capture_output=True, text=True, timeout=60).stdout
    except Exception as e:
        print(f"  查询进程失败: {e}")
        return [], []
    for line in out.splitlines():
        m1 = re.search(r"--symbols\s+(\S+)", line)
        m2 = re.search(r"--depth-symbols\s+(\S+)", line)
        if m1:
            return ([s for s in m1.group(1).split(",") if s],
                    [s for s in m2.group(1).split(",") if s] if m2 else [])
    return [], []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    want_sym, want_dep = authoritative()
    cur_sym, cur_dep = live_lists()

    print("=" * 96)
    print("H136  重启 Aster 采集（USD1 + 补齐深度缺口）")
    print("=" * 96)
    print(f"  权威源（看护脚本）: book {len(want_sym)} 币   depth {len(want_dep)} 币")
    print(f"  当前在跑          : book {len(cur_sym)} 币   depth {len(cur_dep)} 币")
    if cur_sym:
        print(f"\n  新增 book : {sorted(set(want_sym) - set(cur_sym))}")
        print(f"  新增 depth: {sorted(set(want_dep) - set(cur_dep))}")
        lost_b = sorted(set(cur_sym) - set(want_sym))
        lost_d = sorted(set(cur_dep) - set(want_dep))
        if lost_b or lost_d:
            print(f"\n  ⚠️ 会丢符号！book={lost_b} depth={lost_d}")
            print("     （按设计不该丢 —— 权威名单只增不减。若这里非空请先核对）")

    if not a.apply:
        print("\n  （预览）加 --apply 执行重启")
        return 0

    if not RESEARCH.is_dir():
        print(f"  错误：采集工作目录不存在 {RESEARCH}")
        return 1
    if not PYEXE.is_file():
        print(f"  错误：python 不存在 {PYEXE}")
        return 1

    print("\n  [1/3] 停止现有采集进程 …")
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Where-Object { $_.CommandLine -like '*aster_ws_ingest*' } | "
         "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
        capture_output=True, text=True, timeout=60)
    time.sleep(4)
    left = live_lists()[0]
    print(f"        剩余进程 book 名单长度 = {len(left)}")

    print("  [2/3] 用权威名单启动 …")
    cmd = [str(PYEXE), "services\\aster_ws_ingest.py",
           "--symbols", ",".join(want_sym),
           "--depth-symbols", ",".join(want_dep)]
    logdir = ROOT / "logs"
    out = open(logdir / "aster-depth-ingest.out.log", "a", encoding="utf-8")
    err = open(logdir / "aster-depth-ingest.err.log", "a", encoding="utf-8")
    # 脱离父进程：DETACHED_PROCESS 让采集不随本脚本退出而结束
    DETACHED = 0x00000008
    p = subprocess.Popen(cmd, cwd=str(RESEARCH), stdout=out, stderr=err,
                         creationflags=DETACHED | subprocess.CREATE_NEW_PROCESS_GROUP)
    print(f"        已启动 pid={p.pid}  cwd={RESEARCH}")

    print("  [3/3] 等待并核对 …")
    time.sleep(20)
    now_sym, now_dep = live_lists()
    print(f"        现在 book {len(now_sym)} 币   depth {len(now_dep)} 币")
    ok = set(want_sym) <= set(now_sym) and set(want_dep) <= set(now_dep)
    print(f"        {'✅ 一致' if ok else '❌ 不一致，需复查'}")
    print(f"\n  新增的 USD1：{[s for s in now_sym if s.endswith('USD1')]}")
    print("  数据落地需 1~2 分钟，之后用 SQL 核对 asterdex_book_ticker/depth_snapshots")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
