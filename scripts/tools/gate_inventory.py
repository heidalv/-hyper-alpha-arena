"""Gate / skip-reason inventory: find EVERY code that can block or alter a trade.

Why: `drift_veto` (1,287 fires/tick-window) was invisible to me for 37 rounds.
It was found only because `rules_check.py` flagged an unrecognised skip key.
That was luck. This tool makes it systematic:

  1. scan the market-maker source for every string ever assigned to
     `skip`, `reason`, `exit_path`, `exit_action`, or used with `_skip_why`
  2. collect the ones that ACTUALLY appear at runtime (status skip_counts
     + ledger meta)
  3. diff the two sets:
       - IN CODE but NEVER SEEN  -> dead / unreachable branch (suspicious)
       - SEEN but NOT IN CODE    -> dynamic name (needs tracing)
  4. print a complete inventory grouped by "blocks entry" vs "labels exit"

Output is the authoritative gate list for the aperture question.
"""
from __future__ import annotations

import io
import json
import re
import sys
import time
from pathlib import Path

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
DSN = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
LANE = "mm_asterdex"
MM = ROOT / "backend" / "services" / "market_maker"

# patterns that mean "this string can block or relabel a trade"
PATTERNS = [
    re.compile(r'_skip_why\s*=\s*"([^"]+)"'),
    re.compile(r'dec\.skip\s*=\s*"([^"]+)"'),
    re.compile(r'dec\.skip\s*=\s*f"([^"{]+)'),
    re.compile(r'"reason"\s*:\s*"([^"]+)"'),
    re.compile(r'dec\.exit_path\s*=\s*"([^"]+)"'),
    re.compile(r'dec\.exit_path\s*=\s*f"([^"{]+)'),
    re.compile(r'"exit_path"\s*:\s*"([^"]+)"'),
    # [T47] 补齐遗漏的赋值形式 —— 第一版漏了这些，于是
    # `maker_working` / `holding` / `maker_edge` / `maker_risk`
    # 被误判成"运行时出现但源码没匹配到"。
    # 它们的真实写法是 `dec.skip = f"{...}({...})"` 或
    # `dec.skip = <expr>`（三元/变量拼接）。
    re.compile(r'dec\.skip\s*=\s*([a-z_]*maker_working)'),
    re.compile(r'dec\.skip\s*=\s*"(holding)"'),
    re.compile(r'dec\.skip\s*=\s*"(maker_edge)"'),
    re.compile(r'dec\.skip\s*=\s*"(maker_risk)"'),
    re.compile(r'_skip_why\s*=\s*"(maker_edge)"'),
    re.compile(r'"reason"\s*:\s*"(maker_risk)"'),
    re.compile(r'"exit_path"\s*:\s*"(maker_edge)"'),
    # 三元/拼接形式：把标识符本身也收进来
    re.compile(r'(?:skip|reason|exit_path)\s*[:=]\s*[^"\n]*"(maker_[a-z_]+)"'),
]


def main() -> int:
    code_hits: dict = {}
    # [T48] **f-string 前缀是"动态名族"，不能当死分支判据**。
    #   反例：`f"regime_{regime or 'stale'}_no_entry"` 被第一版正则截成字面量
    #   `"regime_"`，于是 `regime_` 被误报为"从未触发"；
    #   而运行时实际是 `regime_R4_no_entry`（skip_counts 里确实有）。
    #   ⇒ 记录其"前缀"，并在死分支判定里**排除整个前缀族**。
    dyn_prefixes: set = set()
    for py in sorted(MM.glob("*.py")):
        try:
            lines = py.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, ln in enumerate(lines, 1):
            for pat in PATTERNS:
                for m in pat.finditer(ln):
                    name = m.group(1)
                    # 若该匹配来自 f-string（同行含 f" 且名字以 _ 结尾），视为前缀
                    if 'f"' in ln and name.endswith("_"):
                        dyn_prefixes.add(name)
                        continue
                    code_hits.setdefault(name, []).append(f"{py.name}:{i}")

    st = json.loads((ROOT / "logs" / "mm_lane_status.json")
                    .read_text(encoding="utf-8", errors="replace"))
    runtime_skips = dict(st.get("skip_counts") or {})

    with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(f"""
            SELECT coalesce(meta_json->>'exit_path','(entry)'), count(*)
            FROM lane_ledger WHERE lane_id='{LANE}' AND event='fill'
              AND ts > now() - interval '24 hours' GROUP BY 1
        """)
        ledger_paths = dict(cur.fetchall() or [])

    print("=" * 94)
    print("闸门 / 跳过原因 全量清单")
    print("=" * 94)
    print(f"  源码里出现的字符串      : {len(code_hits)}")
    print(f"  运行时 skip_counts 里的 : {len(runtime_skips)}")
    print(f"  账本 24h 里的 exit_path : {len(ledger_paths)}")

    print()
    print("=" * 94)
    print("【A】在源码里、且运行时**出现过**（活跃闸门/标签）")
    print("=" * 94)
    live = sorted(set(code_hits) & (set(runtime_skips) | set(ledger_paths)))
    print(f"  {'name':<34}{'skip次数':>10}{'24h腿数':>9}  定义位置")
    for k in live:
        print(f"  {k[:33]:<34}{runtime_skips.get(k, 0):>10}"
              f"{ledger_paths.get(k, 0):>9}  {code_hits[k][0]}")

    print()
    print("=" * 94)
    print("【B】运行时出现过、但**源码里没匹配到**（动态名/需追查）")
    print("=" * 94)
    unknown = sorted(set(runtime_skips) - set(code_hits))
    if unknown:
        for k in unknown:
            print(f"  ⚠️ {k:<32} skip={runtime_skips[k]}")
    else:
        print("  （无 —— 所有运行时 skip 都能在源码里找到定义）")

    print()
    print("=" * 94)
    print("【C】源码里有、运行时**从未出现**（死分支 / 不可达）")
    print("=" * 94)
    dead = sorted(set(code_hits) - set(runtime_skips) - set(ledger_paths))
    for k in dead:
        print(f"  · {k[:44]:<46}{code_hits[k][0]}")

    print()
    print("=" * 94)
    print("结论")
    print("=" * 94)
    print(f"  活跃闸门 {len(live)} 个；未识别 {len(unknown)} 个；死分支 {len(dead)} 个")
    if unknown:
        print("  ⚠️ 存在未识别项 ⇒ 必须先追查它们的作用，再谈'没有新增门禁'")
    else:
        print("  ✅ 所有运行时闸门都有源码定义 ⇒ 口径清单可信")

    # [T48] 落一份机器可读清单，供 classify_dead_branches.py 消费并把
    # "从未触发"逐条归因为「正常」或「待查」。
    try:
        (ROOT / "logs" / "_gate_inventory.json").write_text(
            json.dumps({
                "as_of": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "live": sorted(live),
                "unknown": sorted(unknown),
                "dead": [[k, (code_hits[k] or [""])[0]] for k in dead],
                "runtime_skips": runtime_skips,
                "ledger_paths": ledger_paths,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        print("  ✓ 清单已写 logs/_gate_inventory.json")
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ 写清单失败: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
