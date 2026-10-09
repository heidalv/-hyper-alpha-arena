# -*- coding: utf-8 -*-
"""[H149 2026-09-21] applier 的「生效核对」共享模块 —— 消灭"脚本说已生效、实盘没变"。

# 事故模式（本仓库反复出现）

`scripts/mm_apply_*.py` 这一族脚本写注册表后会打印「已写入，≤60s 内热采用」。
**但对 env 白名单里的键，这句话是错的**：`apply_env_param_overrides()`（F280）
读的是 `os.getenv(...)`，即**进程启动时**由 `load_dotenv` 灌进 `os.environ` 的
冻结值 ⇒ 改 `.env` 文件对已运行进程无效，必须重启。

实测（2026-09-21）：`.env` 的 `MM_SPREAD_MULT` 0.9 → 0.5 后，
worker 心跳**连续 100 秒**仍是 0.9。

历史事故：F189（改了没生效）、F280（本模块来源）、F287（注册表 0.3 / 消费者 0.95）、
F292（同 F287 类）—— **都是同一个根因的不同表现**。

# 本模块给 applier 用的两个能力

  1. `env_whitelist()` —— 参数 → env 变量名，判断某个键是否"必须重启"
  2. `verify_effective(expect, *, timeout)` —— **读运行态心跳**确认参数真的生效了；
     没生效就返回 False 并给出可执行的原因（而不是假装成功）

# 用法（在 applier 写完注册表之后）

    from h149_effective_check import verify_effective, env_whitelist
    ok, msg = verify_effective({"spread_mult": 0.5})
    print(msg)
    if not ok:
        return 1        # 让脚本以非零退出，调用方（调度器/人）能看见失败

# 为什么必须读心跳而不是读注册表

注册表是**意图**，心跳是**事实**。两者分叉过至少 4 次（F287/F292 等）。
本模块只认事实。
"""
from __future__ import annotations

import inspect
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "logs" / "mm_lane_status.json"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 心跳里 `params` / `limits` 两个字典覆盖了绝大多数可调项
_SECTIONS = ("params", "limits")


def env_whitelist() -> dict:
    """参数 -> `MM_*` 环境变量。这些键改了**必须重启** worker。"""
    try:
        from backend.services.market_maker.runner import apply_env_param_overrides
        src = inspect.getsource(apply_env_param_overrides)
        return {m.group(1): m.group(2)
                for m in re.finditer(r'\(\s*"([a-z_]+)"\s*,\s*"(MM_[A-Z_]+)"\s*\)', src)}
    except Exception:
        return {}


def read_status() -> dict:
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def effective_values(keys) -> dict:
    """从心跳里读这些键的**实际生效值**（params 优先，其次 limits）。"""
    st = read_status()
    out = {}
    for k in keys:
        for sec in _SECTIONS:
            d = st.get(sec)
            if isinstance(d, dict) and k in d:
                out[k] = d[k]
                break
    return out


def needs_restart(keys) -> list:
    """返回这些键里"必须重启才生效"的那些。"""
    wl = env_whitelist()
    return [k for k in keys if k in wl]


def verify_effective(expect: dict, *, timeout: float = 150.0,
                     poll: float = 5.0) -> tuple:
    """轮询心跳，确认 `expect` 里每个键都等于期望值。

    返回 `(ok, message)`。message 里包含**每个键的实际值**与是否属于白名单，
    便于直接定位"为什么没生效"。
    """
    keys = list(expect)
    wl = needs_restart(keys)
    t0 = time.time()
    last = {}
    while time.time() - t0 < timeout:
        last = effective_values(keys)
        if all(_same(last.get(k), v) for k, v in expect.items()):
            return True, (f"[生效核对] ✓ {len(keys)} 项已生效"
                          f"（{time.time()-t0:.0f}s）："
                          + ", ".join(f"{k}={last.get(k)}" for k in keys))
        time.sleep(poll)

    lines = [f"[生效核对] ✗ {timeout:.0f}s 内未生效："]
    for k, v in expect.items():
        got = last.get(k)
        tag = "  ← **env 白名单键，必须重启 worker**" if k in wl else ""
        lines.append(f"    {k}: 期望 {v!r}  实际 {got!r}{tag}")
    if wl:
        lines.append("    ⇒ 修法：重启 MM worker（"
                     "`schtasks /Run /TN \"DSH_MM_WORKER\"` 前先杀进程），"
                     "或把该键从 env 白名单移除（让注册表成为运行时唯一权威）。")
    return False, "\n".join(lines)


def _same(a, b) -> bool:
    """容错比较：数值按浮点、布尔按真值、其余按字符串。"""
    if a is None or b is None:
        return a is b
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    try:
        return abs(float(a) - float(b)) < 1e-9
    except Exception:
        return str(a) == str(b)


def main() -> int:
    """自检：打印白名单 + 当前关键参数的实际生效值。"""
    wl = env_whitelist()
    print("=" * 92)
    print("H149  applier 生效核对 · 自检")
    print("=" * 92)
    print(f"\n【env 白名单】{len(wl)} 个键（改了必须重启 worker）")
    for k, e in sorted(wl.items()):
        print(f"    {k:<24} {e}")
    keys = list(wl) + ["compound_ratio", "stop_loss_bp", "stop_loss_vol_min",
                       "timeout_exit_maker_only", "max_one_side_seconds",
                       "min_hold_seconds", "daily_loss_stop_pct"]
    eff = effective_values(keys)
    st = read_status()
    print(f"\n【当前实际生效值】（心跳 params/limits，心跳里的 symbols="
          f"{st.get('symbols')}）")
    for k in keys:
        tag = " [白名单]" if k in wl else ""
        print(f"    {k:<24} {eff.get(k)!r}{tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
