# -*- coding: utf-8 -*-
"""[H147 2026-09-21] 参数生效路径审计：哪些键改了必须重启、哪些能热更新。

# 为什么必须写下来

本轮实测踩到：把 `.env` 的 `MM_SPREAD_MULT` 从 0.9 改成 0.5 后，**正在跑的 worker
心跳连续 100 秒仍是 0.9**。根因是 `apply_env_param_overrides()`（F280）读的是
`os.getenv(...)` —— 那是**进程启动时**由 `load_dotenv` 灌进 `os.environ` 的冻结值，
改文件对已运行进程无效。

⇒ 后果：`scripts/mm_apply_*.py` 这一族"改完就能生效"的脚本，如果改的是白名单里的键，
**实际上不生效**（而它们多数会打印"已写入，≤60s 内热采用" ⇒ **误导**）。
这正好能解释历史上反复出现的「改了但没生效」（F189/F280/F287/F292 都撞过）。

# 本脚本做什么

  1. 从 `apply_env_param_overrides` 的源码里抽出 `参数 -> env 变量` 白名单
  2. 扫描 `scripts/mm_apply_*.py` / `h1*.py` 的 applier，列出各自想改哪些键
  3. 交叉标注：**改的是白名单键 ⇒ 必须重启 worker**；否则热更新即可
  4. 给出一个统一的"安全应用"建议（改完白名单键后自动重启）

用法：
    .venv\\Scripts\\python.exe scripts\\h147_param_wiring_audit.py
"""
from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def whitelist() -> dict:
    """参数 -> env 变量。"""
    from backend.services.market_maker.runner import apply_env_param_overrides
    src = inspect.getsource(apply_env_param_overrides)
    out = {}
    for m in re.finditer(r'\(\s*"([a-z_]+)"\s*,\s*"(MM_[A-Z_]+)"\s*\)', src):
        out[m.group(1)] = m.group(2)
    return out


def appliers() -> dict:
    """applier 脚本 -> 它想写的参数字典（粗略抽取 TARGET/NEW 里的键）。"""
    out = {}
    for pat in ("mm_apply_*.py", "h1[0-9][0-9]_*.py"):
        for f in sorted((ROOT / "scripts").glob(pat)):
            txt = f.read_text(encoding="utf-8", errors="replace")
            keys = set()
            # TARGET = {"k": v} / NEW = {"k": v}
            for m in re.finditer(r"^(TARGET|NEW)\s*=\s*\{(.*?)^\}", txt,
                                 flags=re.M | re.S):
                for km in re.finditer(r'"([a-z_]+)"\s*:', m.group(2)):
                    keys.add(km.group(1))
            if keys:
                out[f.name] = sorted(keys)
    return out


def main() -> int:
    wl = whitelist()
    apps = appliers()

    print("=" * 96)
    print("H147  参数生效路径审计")
    print("=" * 96)
    print(f"\n【env 白名单】{len(wl)} 个键 —— 改了**必须重启 worker** 才生效")
    for k, e in sorted(wl.items()):
        print(f"    {k:<24} {e}")

    print(f"\n【applier 脚本】{len(apps)} 个 —— 标注各自是否踩到白名单")
    print(f"  {'脚本':<32} {'要改的键':<44} 生效路径")
    print("  " + "-" * 92)
    risky = []
    for name, keys in sorted(apps.items()):
        hit = [k for k in keys if k in wl]
        path = "**必须重启**" if hit else "热更新即可"
        if hit:
            risky.append((name, hit))
        ks = ",".join(keys)
        print(f"  {name:<32} {ks[:42]:<44} {path}")

    print(f"\n【结论】")
    print(f"  · {len(risky)} 个 applier 改的是白名单键 ⇒ 它们**改了不重启就不生效**：")
    for name, hit in risky:
        print(f"      {name:<32} 白名单键: {hit}")
    print(f"\n  ⇒ 任何 applier 改完白名单键后**必须重启 worker**，")
    print(f"     否则会出现「脚本说已生效、实盘没变」的静默偏差（历史事故 F189/F280/F287/F292）。")
    print(f"  ⇒ 修法二选一：① applier 里加自动重启；② 把该键从 env 白名单移除，")
    print(f"     让它只由注册表热更新（注册表是运行时权威）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
