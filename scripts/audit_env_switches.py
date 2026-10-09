# -*- coding: utf-8 -*-
"""[F373 2026-09-18] `.env` 开关**有效性**审计：有没有"设了却没人读"的死开关。

由来：本次复查反复发现"看着有、其实没生效"的能力（F367 锚点没接消费端、F371 车道撞第二道墙、
F370 变量集漂移）。这一类还有一个廉价的系统性入口：**`.env` 里设了值，但代码里没人读它**
⇒ 运维以为"我开了这个功能"，实际什么都没有发生。

方法（只读；**只打印键名，绝不打印值**）：
  1. 解析 `.env` 的键名；
  2. 全仓扫描 `os.getenv("K")` / `os.environ.get("K")` / `os.environ["K"]` /
     `env_registry` 声明 / `settings.py` 同名常量；
  3. 输出三类：
     - **死开关**：`.env` 有、代码零引用（含注册表与 settings）；
     - **只靠默认值**：代码引用、`.env` 未设（列出其中与安全/风控/实盘强相关的）；
     - 一致项计数。
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

#: 只看这些目录（排除备份/审计副本，避免重复命中造成假"有引用"）
SCAN_DIRS = ["backend", "scripts"]
SKIP_PARTS = ("_audit_ml", "haa_baseline_check", "_p16_backup", "node_modules", ".venv")

#: 与安全/实盘强相关的键（"只靠默认值"时重点列出）
CRITICAL_HINTS = ("LIVE", "REAL", "ALLOW", "ENABLE", "DISABLE", "KILL", "RISK",
                  "STOP", "LIMIT", "GATE", "FREEZE", "PROMOTION", "WFO", "SCALP",
                  "MIDLONG", "LEVERAGE", "CAPITAL", "DRY", "PAPER")


def env_keys() -> dict:
    """返回 {键: 是否已注释}。**不返回值**。"""
    out = {}
    p = ROOT / ".env"
    if not p.exists():
        return out
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if not s or "=" not in s:
            continue
        commented = s.startswith("#")
        s2 = s.lstrip("#").strip()
        if "=" not in s2:
            continue
        k = s2.split("=", 1)[0].strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k):
            out[k] = commented
    return out


def code_refs() -> dict:
    """返回 {键: [文件:行]}，扫 getenv/environ/注册表/settings 常量。"""
    refs: dict = {}
    pat_getenv = re.compile(r"""(?:os\.getenv|os\.environ\.get|os\.environ\[)\s*\(?\s*["']([A-Za-z_][A-Za-z0-9_]*)["']""")
    pat_env = re.compile(r"""["']([A-Z][A-Z0-9_]{3,})["']""")
    for d in SCAN_DIRS:
        for f in (ROOT / d).rglob("*.py"):
            if any(s in f.as_posix() for s in SKIP_PARTS):
                continue
            try:
                txt = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            rel = f.relative_to(ROOT).as_posix()
            for m in pat_getenv.finditer(txt):
                refs.setdefault(m.group(1), []).append(f"{rel}:{txt[:m.start()].count(chr(10)) + 1}")
            # 注册表 / settings：整文件里出现的全大写常量串
            if f.name in ("env_registry.py", "settings.py") or "env_registry" in rel:
                for m in pat_env.finditer(txt):
                    refs.setdefault(m.group(1), []).append(f"{rel}:{txt[:m.start()].count(chr(10)) + 1}")
    return refs


def main() -> int:
    ek = env_keys()
    refs = code_refs()
    print("=" * 92)
    print(".env 开关有效性审计（只打印键名，不打印值）")
    print("=" * 92)
    active = {k for k, c in ek.items() if not c}
    commented = {k for k, c in ek.items() if c}
    print(f"  .env 键总数={len(ek)}（生效 {len(active)}，被注释 {len(commented)}）")
    print(f"  代码中引用到的键={len(refs)}")

    dead = sorted(k for k in active if k not in refs)
    dead_c = sorted(k for k in commented if k not in refs and len(k) > 4)
    print(f"\n① **死开关**（.env 生效但代码零引用）：{len(dead)}")
    for k in dead:
        print(f"    {k}")
    if dead_c:
        print(f"\n   （被注释且代码零引用：{len(dead_c)}，通常为历史遗留，列出前 10）")
        for k in dead_c[:10]:
            print(f"    # {k}")

    only_default = sorted(k for k in refs if k not in ek)
    crit = [k for k in only_default if any(h in k for h in CRITICAL_HINTS)]
    print(f"\n② **只靠代码默认值**（代码引用、.env 未设）：{len(only_default)}，其中与安全/实盘相关 {len(crit)}")
    for k in crit[:25]:
        print(f"    {k:<44} 引用点 {refs[k][:1]}")

    print("\n" + "=" * 92)
    print("结论")
    print("=" * 92)
    if dead:
        print(f"  ⚠️ {len(dead)} 个键在 .env 里生效但无人读取 ⇒ 可能是"
              f"『设了却无效』的开关（需逐个判定：是否仅供外部脚本/运维使用）")
    else:
        print("  ✅ 没有『.env 生效但代码零引用』的键")
    print(f"  ②类不算缺陷（用默认值是正常做法），但**安全/实盘相关**的 {len(crit)} 个键"
          f"建议在文档里显式写出默认值，避免'以为关了其实是开的'。")
    print("  注意：本审计只看**代码引用**，不判断引用点是否在当前运行路径上；")
    print("        引用存在但调用链已死的开关，需靠 §10『算而不读』那一类分析。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
