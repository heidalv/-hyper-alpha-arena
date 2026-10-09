# -*- coding: utf-8 -*-
"""[h765 2026-10-03] 动态名单同步器:深度只给"在宇宙的币",盘口给宽价差候选。

用户决策(10-03 20:3x):
  · **深度(20 档)只给"进宇宙的币"采**(重,以前 3 天涨到 79GB)
  · **保留 2 天**(retention 任务已含 `--days-depth 2` ✅)
  · 币宇宙是**动态**的:全交易所成交额前 100 → 其中价差 ≥5bp(第一级,`h764`),
    再经精挑过滤器进宇宙(第二级)——名单时时变化。

本脚本把两个名单写成**动态**的:
  · `$DepthSymbols` = 车道当前宇宙(lane_registry.meta.symbols)
  · `$Symbols`      = 宇宙 ∪ 第一级宽价差名单(上限 40)∪ 参照集(BTC/ETH/USD1 实验)

为什么要扩 `$Symbols`:第二级精挑需要**实时盘口**才能评估一个币(价差/可行性);
只订阅在宇宙的币 ⇒ 选择器永远看不到候选(实测池子被卡在 7 个的机制之一)。

用法:
    python h765_sync_dynamic_symbols.py            # 写入(名单变化时提示重启采集)
    python h765_sync_dynamic_symbols.py --check     # 只校验
    python h765_sync_dynamic_symbols.py --restart   # 写入并在变化时重启采集进程
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WATCHDOG = Path(__file__).resolve().parent / "aster-depth-watchdog.ps1"
SCREEN = ROOT / "data" / "vol_top20.json"
LANE = os.environ.get("MM_LANE", "mm_asterdex")

# 参照集:永远采集(便宜,用于对照/基准;USD1 是费率实验,结论未出前不撤)
REFERENCE = ["BTCUSDT", "ETHUSDT", "SOLUSD1", "BTCUSD1", "ETHUSD1"]
WIDE_CAP = 40          # 宽价差候选上限(控制采集负载)
TOTAL_CAP = 60         # 订阅名单总上限

RE_SYMBOLS = re.compile(r"\[string\]\$Symbols = '([^']*)',")
RE_DEPTH = re.compile(r"\[string\]\$DepthSymbols = '([^']*)',")
# [h765] **实际采集器** standby_ingest.py 里也有硬编码名单(实测 35/28),
# 只改看门狗无效 —— 真正订阅的是它。两个文件都要同步。
STANDBY = Path(__file__).resolve().parent / "standby_ingest.py"
RE_SB_SYMBOLS = re.compile(r"SYMBOLS = \((.*?)\)\n", re.S)
RE_SB_DEPTH = re.compile(r"DEPTH_SYMBOLS = \((.*?)\)\n", re.S)


def _fmt_py_list(syms: list, indent: str = "           ") -> str:
    """把名单写成 standby_ingest.py 里的多行字符串拼接形式。"""
    lines, cur = [], ""
    for s in syms:
        piece = s + ","
        if len(cur) + len(piece) > 78:
            lines.append(cur)
            cur = ""
        cur += piece
    if cur:
        lines.append(cur)
    body = "".join(f'{indent}"{ln}"\n' for ln in lines)
    return "(\n" + body + indent[:-3] + ")"


def _universe() -> list:
    """车道当前宇宙(权威源=lane_registry.meta.symbols)。"""
    try:
        sys.path.insert(0, str(ROOT))
        from backend.services import lane_registry as reg
        meta = (reg.get_lane(LANE) or {}).get("meta") or {}
        syms = meta.get("symbols") or []
        return [str(s).upper() + "USDT" for s in syms if s]
    except Exception as e:
        print(f"  ⚠ 读宇宙失败({type(e).__name__})⇒ 深度名单保持原样")
        return []


def _wide() -> list:
    """第一级筛选器的宽价差名单(h764 输出)。"""
    try:
        d = json.loads(SCREEN.read_text(encoding="utf-8"))
        return [str(s).upper() + "USDT" for s in (d.get("wide") or [])]
    except Exception:
        return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--restart", action="store_true", help="名单变化时重启采集进程")
    args = ap.parse_args()

    src = WATCHDOG.read_text(encoding="utf-8-sig")
    m_sym, m_dep = RE_SYMBOLS.search(src), RE_DEPTH.search(src)
    if not m_sym or not m_dep:
        print("ERR 未定位到 $Symbols / $DepthSymbols")
        return 3
    cur_sym = [s for s in m_sym.group(1).split(",") if s]
    cur_dep = [s for s in m_dep.group(1).split(",") if s]

    uni = _universe()
    wide = _wide()[:WIDE_CAP]
    # 深度 = 宇宙(用户决策);宇宙读失败时保持原深度名单,绝不误清空
    want_dep = uni if uni else cur_dep
    # 订阅 = 宇宙 ∪ 宽价差 ∪ 参照集(去重保序:宇宙优先)
    ordered: list = []
    for s in [*uni, *wide, *REFERENCE]:
        if s and s not in ordered:
            ordered.append(s)
    want_sym = ordered[:TOTAL_CAP]

    print(f"宇宙({len(uni)}): {[s[:-4] for s in uni]}")
    print(f"宽价差名单({len(wide)}): {[s[:-4] for s in wide[:15]]}{' ...' if len(wide) > 15 else ''}")
    print(f"当前: Symbols={len(cur_sym)} DepthSymbols={len(cur_dep)}")
    print(f"目标: Symbols={len(want_sym)} DepthSymbols={len(want_dep)}")

    extra = set(want_dep) - set(want_sym)
    if extra:
        print(f"ERR 深度名单含未订阅的币: {sorted(extra)}")
        return 4

    changed = (cur_sym != want_sym) or (cur_dep != want_dep)
    if args.check:
        print("校验:", "一致 ✅" if not changed else "不一致 ❌")
        if changed:
            print("  订阅差集:", sorted(set(want_sym) ^ set(cur_sym))[:20])
            print("  深度差集:", sorted(set(want_dep) ^ set(cur_dep))[:20])
        return 0 if not changed else 1

    if not changed:
        print("看门狗名单无变化;仍同步实际采集器 standby_ingest.py(下方)")
    else:
        new_src = RE_SYMBOLS.sub(lambda _: f"[string]$Symbols = '{','.join(want_sym)}',", src, count=1)
        new_src = RE_DEPTH.sub(lambda _: f"[string]$DepthSymbols = '{','.join(want_dep)}',", new_src, count=1)
        WATCHDOG.write_text(new_src, encoding="utf-8")
        print("✓ 已写入看门狗名单")
        print("  深度新增:", sorted(set(want_dep) - set(cur_dep))[:12])
        print("  深度移除:", sorted(set(cur_dep) - set(want_dep))[:12])
        print("  订阅新增:", sorted(set(want_sym) - set(cur_sym))[:12])

    # [h765] 同步**实际采集器** standby_ingest.py(真正订阅名单的地方)
    try:
        sb = STANDBY.read_text(encoding="utf-8")
        m1, m2 = RE_SB_SYMBOLS.search(sb), RE_SB_DEPTH.search(sb)
        if m1 and m2:
            sb_new = RE_SB_SYMBOLS.sub(lambda _: "SYMBOLS = " + _fmt_py_list(want_sym,
                                     "           ") + "\n", sb, count=1)
            sb_new = RE_SB_DEPTH.sub(lambda _: "DEPTH_SYMBOLS = " + _fmt_py_list(want_dep,
                                   "                 ") + "\n", sb_new, count=1)
            STANDBY.write_text(sb_new, encoding="utf-8")
            print(f"✓ 已写入 standby_ingest.py(SYMBOLS={len(want_sym)} DEPTH={len(want_dep)})")
        else:
            print("  ⚠ 未能在 standby_ingest.py 定位 SYMBOLS/DEPTH_SYMBOLS")
    except Exception as e:
        print(f"  ⚠ 写 standby_ingest 失败: {type(e).__name__}: {e}")

    if args.restart:
        # 名单要生效必须让采集进程重启(看门狗会在 60s 内用新名单拉起)
        # [h770 2026-10-03] **必须加 CREATE_NO_WINDOW**:Python 的 subprocess 在
        # Windows 上会给控制台子进程**新建一个可见控制台窗口** —— 实测这就是
        # "每 5 分钟弹一次黑框"的最后一个来源(本任务每 5 分钟跑一次)。
        try:
            import subprocess
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'aster_ws_ingest' } | "
                 "ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }"],
                capture_output=True, text=True, timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            print("  已请求重启采集进程:", (out.stdout or "").strip() or "(无匹配进程)")
        except Exception as e:
            print(f"  ⚠ 重启采集失败: {type(e).__name__}: {e}")
    else:
        print("  (加 --restart 才会重启采集进程使名单生效)")
    (ROOT / "data" / "watchdog_symbols_last.json").write_text(
        json.dumps({"ts": time.time(), "symbols": want_sym, "depth": want_dep},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
