# -*- coding: utf-8 -*-
"""审计工具：安全闸**可达性**扫描（目标第 2 轮，(d) 项）。

问三件事：
  1. 这个闸**被调用了吗**？（调用点计数，排除自身定义与测试）
  2. 它**异常时是否 fail-open**（`except: return True`）？——异常静默放行是典型静默失效。
  3. 它**有没有真的返回过「拦」**？（是否含 `return False` / 返回 (False, ...)）

用法：
  .venv\\Scripts\\python.exe backend/scripts/audit_gate_reachability.py [--json PATH]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# [2026-09-16 调研轮7] Windows 控制台默认 cp936(GBK)：打印 ✅/❌ 会抛 UnicodeEncodeError
# → 脚本 rc=1 → 例行审计误报 FAIL、真失败被淹没。守卫 stdout/stderr 为 UTF-8。
try:  # pragma: no cover
    import sys as _sys_utf8

    _sys_utf8.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys_utf8.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 视为「安全闸/风控」的函数名特征
GATE_NAME = re.compile(
    r"(gate|guard|veto|block|allow|can_open|check_.*(risk|limit|exposure|cooldown|budget)|"
    r"circuit|breaker|halt|kill|deny|forbid)",
    re.I,
)
# 明显不是闸的（工具/查询/展示）
NOT_GATE = re.compile(r"(^_?log|^_?fmt|^_?parse|^_?build|^_?get_|^_?list|^_?describe|^_?summar)", re.I)

DEF_RX = re.compile(r"^def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", re.M)
FAILOPEN_RX = re.compile(r"except[^:]*:\s*(?:\n\s*)*(?:#[^\n]*\n\s*)*return\s+(True|None|\(True)")
# 「意图明确」的标记：理由串/注释/docstring 里明说是 fail-open 或有正当理由
INTENT_RX = re.compile(
    r"(fail[-_ ]?open|failopen|未启用|不否决|非开仓动作|证据不齐|no_evidence|bad_flow_fields|"
    r"no_action|disabled|信号字段异常|陈旧)",
    re.I,
)


def scan_module(path: Path):
    """返回该模块内所有候选闸函数及其属性。"""
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    out = []
    defs = [(m.start(), m.group(1)) for m in DEF_RX.finditer(text)]
    starts = [m.start() for m in DEF_RX.finditer(text)]
    for idx, (pos, name) in enumerate(defs):
        if not GATE_NAME.search(name) or NOT_GATE.search(name):
            continue
        end = starts[idx + 1] if idx + 1 < len(starts) else len(text)
        body = text[pos:end]
        # 行号
        lineno = text[:pos].count("\n") + 1
        out.append({
            "name": name,
            "file": str(path.relative_to(ROOT)),
            "line": lineno,
            "fail_open": bool(FAILOPEN_RX.search(body)),
            # 意图明确的 fail-open（理由串/注释里写明）vs **裸 except 放行**（无任何说明）
            "fail_open_documented": bool(FAILOPEN_RX.search(body)) and bool(INTENT_RX.search(body)),
            "has_block": bool(re.search(r"return\s+(False|\(False)", body)),
            "returns_bool": bool(re.search(r"->\s*(bool|Tuple\[bool)", body)),
        })
    return out


IMPORT_RX = re.compile(r"^\s*from\s+[\w\.]+\s+import\s+(\([^)]*\)|[^\n(]+)", re.M | re.S)


def build_alias_map(text: str) -> dict:
    """`from X import gate_name as _alias` → {alias: gate_name}（**含多行括号导入**）。

    修正工具自身的两处盲点（都被契约测试抓到）：
      1. 只数「名字逐字出现」会漏掉**别名导入**的调用
         （实测 is_tier_open_blocked / long_lane_open_allowed / check_fee_budget 均以 `as _x` 调用）；
      2. 首版正则用 `(.+?)$` 不跨行 → **括号跨行导入**（`from X import (\\n  a as _b,\\n)`）
         仍然漏掉，而这正是本仓库的常见写法。
    """
    out = {}
    for m in IMPORT_RX.finditer(text):
        chunk = m.group(1).replace("(", "").replace(")", "").replace("\n", " ")
        for part in chunk.split(","):
            part = part.strip()
            if not part:
                continue
            mm = re.match(r"([A-Za-z_][\w]*)\s+as\s+([A-Za-z_][\w]*)", part)
            if mm:
                out[mm.group(2)] = mm.group(1)
    return out


def count_calls(all_texts, name: str, def_file: str):
    """调用点计数：含**别名导入**后的调用。返回 (总数, 文件列表, 别名命中数)。"""
    pat = re.compile(rf"(?<!def )\b{re.escape(name)}\s*\(")
    total, files, alias_hits = 0, set(), 0
    for rel, text in all_texts.items():
        aliases = [a for a, orig in build_alias_map(text).items() if orig == name]
        apats = [re.compile(rf"\b{re.escape(a)}\s*\(") for a in aliases]
        for line in text.splitlines():
            if line.strip().startswith(("def ", "from ", "import ")):
                continue
            if pat.search(line):
                total += 1
                files.add(rel)
            elif any(p.search(line) for p in apats):
                total += 1
                alias_hits += 1
                files.add(rel)
    return total, sorted(files), alias_hits


def build(root: Path = ROOT) -> dict:
    py = [p for p in (root / "backend").rglob("*.py")
          if ".venv" not in str(p) and "tests" not in str(p)]
    texts = {str(p.relative_to(root)): p.read_text(encoding="utf-8", errors="ignore") for p in py}

    gates = []
    for p in py:
        gates.extend(scan_module(p))
    for g in gates:
        n, files, alias_hits = count_calls(texts, g["name"], g["file"])
        g["calls"] = n
        g["alias_hits"] = alias_hits
        g["call_files"] = files[:6]
    # 去重（同名函数多份实现时各算一份）
    return {"n_py": len(py), "gates": gates}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=str(ROOT / "data" / "gate_reachability.json"))
    args = ap.parse_args()
    rep = build()
    gates = rep["gates"]

    never = [g for g in gates if g["calls"] == 0]
    failopen = [g for g in gates if g["fail_open"]]
    bare = [g for g in failopen if not g["fail_open_documented"]]
    noblock = [g for g in gates if not g["has_block"]]

    print(f"扫描 {rep['n_py']} 个 py 文件，候选闸函数 {len(gates)} 个"
          f"（调用计数**含别名导入**，修正首版盲点）")
    print(f"\n=== A. **从未被调用**（潜在死闸）n={len(never)} ===")
    for g in sorted(never, key=lambda x: x["file"])[:60]:
        svc = "服务层" if "/api/" not in g["file"] and "\\api\\" not in g["file"] else "API路由(可能被装饰器挂载)"
        print(f"  [{svc}] {g['file']}:{g['line']:<5}{g['name']}")
    svc_never = [g for g in never if "api" not in g["file"].split("\\")[-2:-1]
                 and "\\api\\" not in g["file"]]
    print(f"\n  → 其中**服务层**（非 API 路由）{len(svc_never)} 个：")
    for g in sorted(svc_never, key=lambda x: x["file"]):
        print(f"      {g['file']}:{g['line']:<5}{g['name']}"
              f"{'  [fail-open]' if g['fail_open'] else ''}")
    print(f"\n=== B. fail-open 闸门 n={len(failopen)}"
          f"（其中**意图明确/有说明** {len(failopen)-len(bare)} 个，"
          f"**裸 except 静默放行** {len(bare)} 个）===")
    for g in sorted(failopen, key=lambda x: x["file"]):
        tag = "意图明确" if g["fail_open_documented"] else "⚠裸放行"
        print(f"  [{tag}] {g['file']}:{g['line']:<5}{g['name']:<42}calls={g['calls']}")
    if bare:
        print("\n  → **需处理（裸放行，无理由串/无注释）**：")
        for g in bare:
            print(f"      {g['file']}:{g['line']:<5}{g['name']}")
    print(f"\n=== C. 无「拦」分支（永远放行）n={len(noblock)} ===")
    for g in sorted(noblock, key=lambda x: x["file"])[:40]:
        print(f"  {g['file']}:{g['line']:<5}{g['name']:<42}calls={g['calls']}")

    out = Path(args.json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
