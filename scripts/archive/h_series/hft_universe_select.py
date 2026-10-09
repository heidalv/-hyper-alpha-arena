# -*- coding: utf-8 -*-
"""AI 选币 · 超短链路的调度入口

用法：
    # 只计算不写库（看结果）
    python scripts/hft_universe_select.py --dry-run

    # 正式应用（写 lane_registry.meta.symbols + universe）
    python scripts/hft_universe_select.py --lane mm_asterdex

    # 自定义固定币与 AI 位数
    python scripts/hft_universe_select.py --fixed ASTER,XRP,SOL --ai-slots 5

## 为什么要有这个入口

`coin_select_hft.select_universe()` 是纯函数，需要外部驱动。宇宙要"根据实际情况
**自动调配**"（用户 2026-09-20）⇒ 必须能挂调度器周期跑。

## 调度建议

    周期：每 30–60 分钟（远低于点差/深度变化的时间尺度，且 `compute_hft_stats`
          单次约 20s，属重查询，不宜高频）
    注意：**改动 `meta.symbols` 不会重启车道**——车道在下一个 tick 读运行态时生效；
          若车道正在运行且有持仓，被移出宇宙的币不会自动平仓，需人工确认。

## 安全约束

- 默认 **dry-run**（必须显式给 `--lane` 才写库）
- 写入前打印 before/after 差异
- 固定币不参与评分（人工指定、全天候交易）
- LLM 否决失败时 fail-open（不阻塞选币），风险由机械硬闸兜底
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# ⚠️ Windows 计划任务的 stdout 默认是 GBK（cp936），任何非 GBK 字符（如 ✓）都会
#    抛 UnicodeEncodeError **并中断脚本**（实测：选币成功、打印时崩，导致后续落库
#    步骤被跳过）。两个防线：① 强制 stdout UTF-8；② 输出只用 ASCII 标记。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env", override=False)

from backend.services.coin_select_hft import (  # noqa: E402
    apply_to_lane, llm_veto, select_universe,
)

DEFAULT_FIXED = "ASTER,XRP,SOL,DOGE,UNI"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", default="", help="车道 id；给了才写库（否则 dry-run）")
    ap.add_argument("--fixed", default=DEFAULT_FIXED)
    ap.add_argument("--ai-slots", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true", help="强制不写库")
    ap.add_argument("--no-llm", action="store_true", help="跳过 LLM 风险否决")
    ap.add_argument("--top", type=int, default=15, help="打印前 N 名评分")
    args = ap.parse_args()

    fixed = [s.strip().upper() for s in args.fixed.split(",") if s.strip()]

    print(f"[1/4] 机械层：计算统计量（fixed={fixed}, ai_slots={args.ai_slots}）...", flush=True)
    # 首次调用会**强制重算**统计量（绕开 300s 缓存）：本脚本每 30 分钟才跑一次，
    # 必须基于最新统计而不是上次请求留下的缓存。
    pre = select_universe(fixed=fixed, ai_slots=max(args.ai_slots * 4, 12), force_stats=True)

    vetoed = {}
    if not args.no_llm:
        print(f"[2/4] LLM 风险否决（审阅 {len(pre.scored)} 个候选）...", flush=True)
        brief = [
            {k: r.get(k) for k in ("symbol", "spread_med_bp", "spread_p25_bp",
                                   "book_updates", "trades_total")}
            for r in pre.scored
        ]
        vetoed = llm_veto(brief)
        print(f"      否决 {len(vetoed)} 个: {json.dumps(vetoed, ensure_ascii=False)}"
              if vetoed else "      无否决")
    else:
        print("[2/4] 跳过 LLM 否决（--no-llm）")

    print("[3/4] 最终选币 ...", flush=True)
    # force_stats=False：统计量刚在 [1/4] 强制重算过，这里直接用缓存即可（省 20s）
    d = select_universe(fixed=fixed, ai_slots=args.ai_slots, vetoed=vetoed)
    print(f"      固定币 : {d.fixed}")
    print(f"      AI 选币: {d.ai}")
    print(f"      最终宇宙({len(d.symbols)}): {d.symbols}")

    print(f"\n  ── 评分前 {args.top} ──")
    print(f"  {'symbol':<10}{'score':>9}{'spread_bp':>11}{'book_n':>10}{'sel':>5}")
    for r in d.scored[: args.top]:
        print(f"  {r['symbol']:<10}{r['score']:>9.2f}{r['spread_med_bp']:>11.2f}"
              f"{r['book_updates']:>10,}{('*' if r.get('selected') else ''):>5}")
    if d.rejected:
        print(f"\n  ── 被拒 {len(d.rejected)} 个 ──")
        for k, v in list(d.rejected.items())[:10]:
            print(f"  {k:<10} {v}")

    print("\n[4/4] 落库 ...", flush=True)
    if args.dry_run or not args.lane:
        r = apply_to_lane(args.lane or "mm_asterdex", d, dry_run=True)
        print(f"      DRY-RUN（未写库）: before={r.get('before')} -> after={r.get('after')}")
        if not args.lane:
            print("      （未给 --lane，默认只演算）")
    else:
        r = apply_to_lane(args.lane, d)
        print(f"      已写入 {args.lane}: {json.dumps(r, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
