# -*- coding: utf-8 -*-
"""审计工具：配置「实际生效值 vs 预期」+ falsy 吞值扫描（目标第 1 轮）。

起因：9/9 夜大亏的根因之一不是缺规则，而是 `.env` 把 `MIDLONG_MAX_OPEN_POSITIONS`
设成 6（代码默认 4），安全闸**被悄悄放宽**；随后又发现 `_cfg_int` 用 `or default`
把显式 0 吞掉，导致"0=关闭"的语义从未生效。

本脚本把这两类「静默失效」做成可复现的静态审计：

A. 配置三层对齐：`.env` ↔ `settings.py` 默认值 ↔ `env_registry.py` 登记
B. 高危偏差：`.env` 有键但 settings 未定义（**设了不生效**）
C. 死键：settings 定义但全仓无引用
D. 未登记：治理缺口
E. falsy 吞值：`getenv(...) or X` / `getattr(...) or X` / `int(... or ...)`
   —— 对「0/false/空串有意义」的键（开关、上限、阈值、冷却）为高危

用法：
  .venv\\Scripts\\python.exe backend/scripts/audit_config_effective.py [--json PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# [2026-09-16 调研轮7] Windows 控制台默认 cp936(GBK)：打印 ✅/❌ 会抛 UnicodeEncodeError
# → 脚本 rc=1 → 例行审计误报 FAIL、真失败被淹没。守卫 stdout/stderr 为 UTF-8。
try:  # pragma: no cover
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 与「安全/风控/闸门」相关的键名特征（这些键的偏差优先级最高）
SAFETY_HINT = re.compile(
    r"(MAX|MIN|LIMIT|ENABLE|ENABLED|GATE|GUARD|THRESHOLD|RISK|STOP|CAP|COOL|"
    r"BLOCK|VETO|BAN|FLOOR|CEIL|ALLOW|MODE|TRAIL|SL_|_SL|LOCK|PROTECT)",
    re.I,
)
# settings.py 里的定义：KEY: type = os.getenv("KEY", "default")
RE_SETTING = re.compile(
    r"^\s*([A-Z][A-Z0-9_]{2,})\s*(?::\s*[^=]+)?=\s*os\.getenv\(\s*[\"']\1[\"']\s*(?:,\s*([^)]+))?\)",
    re.M,
)
RE_ENV_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
# falsy 吞值：...getenv(...) or X   /  ...getattr(...) or X  /  int(... or ...) / float(... or ...)
# 允许一层嵌套括号（如 os.getenv("K", str(default)) or default）
_NEST = r"(?:[^()]|\([^()]*\))*"
RE_FALSY_GETENV = re.compile(rf"os\.getenv\({_NEST}\)\s*or\s+[^,)\n]+")
RE_FALSY_GETATTR = re.compile(rf"getattr\({_NEST}\)\s*or\s+[^,)\n]+")
RE_FALSY_CAST = re.compile(r"\b(?:int|float)\(\s*[^()\n]*\bor\b[^()\n]*\)")


def _strip_inline_comment(v: str) -> str:
    """去掉行内注释（`#` 不在引号内时截断）——否则 `KEY=false  # 说明` 会被当成整串值。"""
    q = None
    for i, ch in enumerate(v):
        if q:
            if ch == q:
                q = None
            continue
        if ch in "\"'":
            q = ch
        elif ch == "#":
            return v[:i].strip()
    return v.strip()


def normalize(v) -> str:
    """比较用归一化：去引号/空白/行内注释，布尔统一小写。"""
    s = str(v if v is not None else "").strip()
    s = _strip_inline_comment(s)
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1]
    return s.strip().lower()


def parse_env(text: str) -> dict:
    """解析 .env（忽略注释与空行；去引号与行内注释）。"""
    out = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = RE_ENV_LINE.match(s)
        if not m:
            continue
        k, v = m.group(1), m.group(2)
        v = _strip_inline_comment(v)
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k] = v.strip()
    return out


def parse_settings_defaults(text: str) -> dict:
    """{KEY: (default_expr, lineno)}。"""
    out = {}
    for i, line in enumerate(text.splitlines(), 1):
        m = RE_SETTING.match(line)
        if m:
            out[m.group(1)] = ((m.group(2) or "").strip(), i)
    return out


def parse_registry(text: str) -> set:
    return set(re.findall(r"[\"']([A-Z][A-Z0-9_]{2,})[\"']", text))


def find_falsy_sites(text: str):
    """返回 [(lineno, kind, severity, snippet)]。

    严重度判定（关键）：
      - **HIGH**：`getattr(settings, KEY, default) or X` —— 左值已是解析后的
        int/float/bool，显式 0/0.0/False 会被 X 替换（§38.9/2 的 MIDLONG_MAX_OPEN_POSITIONS 即此类）。
      - **LOW** ：`os.getenv(KEY, "d") or X` —— 左值是**字符串**，`"0"`/`"false"` 均为真，
        `or` 实际不会触发（仅当显式设成空串时回落），故通常无害。
    """
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if s.startswith("#"):
            continue
        for m in RE_FALSY_GETATTR.finditer(line):
            snip = m.group(0)[:120]
            sev = "HIGH" if re.search(r"or\s+(default|fallback|-?\d|[\"'])", snip) else "LOW"
            out.append((i, "getattr_or", sev, snip))
        for m in RE_FALSY_GETENV.finditer(line):
            out.append((i, "getenv_or", "LOW", m.group(0)[:120]))
        for m in RE_FALSY_CAST.finditer(line):
            snip = m.group(0)[:120]
            # int(x or y) 里 x 若非字符串来源则同样吞值
            sev = "HIGH" if "getattr(" in snip else "LOW"
            out.append((i, "cast_or", sev, snip))
    return out


def keys_in_snippet(snip: str) -> list:
    return re.findall(r"[\"']([A-Z][A-Z0-9_]{2,})[\"']", snip)


def _is_test_file(p: Path) -> bool:
    """测试文件不算"生产使用"（否则测试里写一次键名就会让死键判定失效）。"""
    s = str(p).replace("\\", "/")
    name = p.name
    return "/tests/" in s or name.startswith("test_") or name.endswith("_test.py")


def scan_usages(py_files, *, include_tests: bool = True):
    """{KEY: [相对路径:行号]}——任何出现处（含 registry/settings）。

    [§54 修复] `include_tests=False` 时跳过 `backend/tests/**`：
    此前 `REENTRY_COOLDOWN_SEC` 因为**测试文件里出现了一次**就被判成"有人在用"，
    从而从死键/近名误配名单里消失（真实的近名误配被测试代码掩盖）。
    """
    use = defaultdict(list)
    for p in py_files:
        if not include_tests and _is_test_file(p):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for k in keys_in_snippet(line):
                use[k].append(f"{p.relative_to(ROOT)}:{i}")
    return use


RE_ENV_READ = re.compile(
    r"os\.(?:getenv|environ\.get)\(\s*[\"']([A-Z][A-Z0-9_]{2,})[\"']"
    r"|os\.environ\[\s*[\"']([A-Z][A-Z0-9_]{2,})[\"']\s*\]"
)

# ── [§54 修复] 动态前缀读取（本工具此前的**盲点**，曾导致一次误报）──
# 例：`os.getenv(f"PC_{param}_{lane.upper()}")`、`os.getenv(f"LLM2_CAP_{scope.upper()}", "")`。
# 这类键在字面上永远搜不到，首版工具会把它们判成"死键"（§54.1 实证：
# `PC_RISK_PER_TRADE_PCT_LONG` 被判死键，实际经 `PC_<PARAM>_<LANE>` 生效）。
RE_ENV_READ_DYNAMIC = re.compile(
    # ① `os.getenv(f"PREFIX_{var}")` / `_env_f(f"PREFIX_{var}")`
    r"(?:os\.(?:getenv|environ\.get)|_env_[a-z]+|_[a-z_]*getenv)\(\s*f[\"']([A-Z][A-Z0-9_]*_)\{[^}]*\}"
    # ② 任意位置的 f-string 前缀：`env_key = f"PREFIX_{period.upper()}"`（再传给 getenv）
    r"|f[\"']([A-Z][A-Z0-9_]*_)\{[^}]*\}"
    # ③ 字符串拼接前缀：`"PREFIX_" + var`
    r"|[\"']([A-Z][A-Z0-9_]*_)[\"']\s*\+"
)


def scan_dynamic_prefixes(py_files) -> dict:
    """{前缀: [相对路径:行号]}——`f"PREFIX_{...}"` / `"PREFIX_" + var` 形式的前缀读取。"""
    out = defaultdict(list)
    for p in py_files:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            for m in RE_ENV_READ_DYNAMIC.finditer(line):
                pref = m.group(1) or m.group(2) or m.group(3)
                if pref:
                    out[pref].append(f"{p.relative_to(ROOT)}:{i}")
    return out


def dynamic_prefix_matches(key: str, prefixes) -> str:
    """该 .env 键是否被某个动态前缀覆盖（返回前缀，否则空串）。"""
    best = ""
    for pref in prefixes:
        if key.startswith(pref) and len(pref) > len(best):
            best = pref
    return best


# ── [§54 修复 2] 后缀拼接读取：`os.getenv(name + "_PAPER")`（decision_fusion_arbiter._f_mode）──
# 这类键（`X_PAPER` / `X_LIVE`）在字面上也搜不到：键名 = 基名 + 后缀，基名是变量。
# 例：`.env FUSION_PROBE_MIN_PWIN_PAPER=0.42` 被判死键，实测 paper 模式**确实读它**。
RE_SUFFIX_READ = re.compile(
    r"os\.(?:getenv|environ\.get)\(\s*[A-Za-z_][\w.]*\s*\+\s*[\"']([A-Z_]{2,})[\"']"
)
# 后缀字面量（`_suffix = "_PAPER"`）与"拼接式 getenv"两种模式**同文件共现**时，
# 视为该文件存在后缀拼接读取（保守取并集：宁可漏判死键，不可误报死键）。
RE_SUFFIX_LITERAL = re.compile(r"[\"'](_[A-Z]{2,})[\"']")
RE_COMPOSED_GETENV = re.compile(r"os\.(?:getenv|environ\.get)\([^)\n]*\+")


def scan_suffix_composed(py_files) -> dict:
    """{后缀: [相对路径:行号]}——`os.getenv(var + _suffix)` 形式的后缀拼接读取。

    实现说明（诚实标注）：先找「拼接式 getenv」所在文件，再收集**同一文件**里的
    `_XXX` 字面量作为候选后缀。这是启发式，因此本类键一律**排除在死键之外**并单列，
    供人工/运行时核验（例：`_f_mode` 的 `_PAPER`/`_LIVE` 已用运行时验证过）。
    """
    out = defaultdict(list)
    for p in py_files:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        lines = text.splitlines()
        sites = [(i, ln) for i, ln in enumerate(lines, 1) if RE_COMPOSED_GETENV.search(ln)]
        if not sites:
            continue
        cands = set()
        for ln in lines:
            for m in RE_SUFFIX_LITERAL.finditer(ln):
                cands.add(m.group(1))
        if not cands:
            continue
        for i, _ln in sites:
            for suf in cands:
                out[suf].append(f"{p.relative_to(ROOT)}:{i}")
    return out


def scan_env_reads(py_files) -> dict:
    """{KEY: [相对路径:行号]}——**直接** os.getenv/os.environ 读取处（不经 settings）。"""
    reads = defaultdict(list)
    for p in py_files:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            for m in RE_ENV_READ.finditer(line):
                k = m.group(1) or m.group(2)
                if k:
                    reads[k].append(f"{p.relative_to(ROOT)}:{i}")
    return reads


RE_HELPER_DEF = re.compile(
    r"^def (_(?:cfg|env)_(?:int|float|bool|str))\s*\(", re.M)


def scan_cfg_helpers(py_files):
    """找出所有 `_cfg_*` / `_env_*` 配置读取助手，并判定是否会吞掉 falsy 值。

    [§55 修复] 只按文本形状判定会把两类**无害**写法误报成危险：
      ① 左值是 `os.getenv(...)` 的**字符串**（`"0"`/`"false"` 为真值）；
      ② 函数先读 `os.getenv(name)` 且做了 `is not None` 检查，之后才回落到
         `getattr(settings, ...) or default`（此时 env 未设 ⇒ settings 也是默认值，`or` 无副作用）。
    故本函数用 AST 只分析**代码**（剔除 docstring），并区分三类：
      - `dangerous`：代码里存在 `getattr(...) or X`（显式 0/False 被吞）；
      - `getenv_or_benign`：仅 `os.getenv(...) or X`（字符串左值，无害）；
      - `env_first_benign`：先读 env 且判 `is not None`（回落分支无害）。
    """
    import ast
    out = []
    for p in py_files:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(text)
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if not re.match(r"_(?:cfg|env)_(?:int|float|bool|str)", node.name):
                continue
            try:
                # 去掉 docstring 再 unparse：否则"修复说明里提到旧写法"会被当成代码命中
                import copy
                _n2 = copy.deepcopy(node)
                if (_n2.body and isinstance(_n2.body[0], ast.Expr)
                        and isinstance(getattr(_n2.body[0], "value", None), ast.Constant)
                        and isinstance(_n2.body[0].value.value, str)):
                    _n2.body = _n2.body[1:] or [ast.Pass()]
                code = ast.unparse(_n2)
            except Exception:
                code = ""
            danger_getattr = bool(re.search(r"getattr\([^)]*\)\s*or\s+", code))
            benign_getenv = bool(re.search(r"os\.getenv\([^)]*\)\s*or\s+", code))
            env_first = bool(re.search(r"os\.getenv\(\s*name\s*\)", code)) and \
                bool(re.search(r"is not None", code))
            if danger_getattr and env_first:
                kind, dangerous = "env_first_benign", False
                reason = ("先读 os.getenv(name) 且判 is not None；回落分支仅在 env 未设时到达"
                          "（此时 settings 也是默认值）⇒ 显式 0 不会被吞")
            elif danger_getattr:
                kind, dangerous = "getattr_or", True
                reason = "getattr(settings) 左值已解析：显式 0/False 会被默认值替换"
            elif benign_getenv:
                kind, dangerous = "getenv_or_benign", False
                reason = "os.getenv 左值是字符串：\"0\"/\"false\" 为真值，仅空串回落（无害）"
            else:
                kind, dangerous, reason = "ok", False, ""
            out.append({
                "file": f"{p.relative_to(ROOT)}:{node.lineno}",
                "fn": node.name,
                "dangerous": dangerous,
                "kind": kind,
                "reason": reason,
                "body": code.strip().replace("\n", " ")[:160],
            })
    return out


def detect_near_miss(dead_keys, live_keys):
    """**近名误配检测**：.env 里"设了但没人读"的键，若与某个"代码在读"的键高度相似，
    基本可判定是拼写/命名不一致（例：`.env REENTRY_COOLDOWN_SEC=60` 而代码读
    `REENTRY_COOLDOWN_SECONDS` → 实际冷却 600s，是意图的 10 倍）。

    返回 [{env_key, candidate, score}]，按相似度降序。
    """
    import difflib
    live = sorted(set(live_keys))
    out = []
    for k in dead_keys:
        m = difflib.get_close_matches(k, live, n=2, cutoff=0.82)
        for c in m:
            out.append({"env_key": k, "candidate": c,
                        "score": round(difflib.SequenceMatcher(None, k, c).ratio(), 3)})
    out.sort(key=lambda x: -x["score"])
    return out


def build_report(root: Path = ROOT) -> dict:
    env_p = root / ".env"
    set_p = root / "backend" / "config" / "settings.py"
    reg_p = root / "backend" / "config" / "env_registry.py"
    env = parse_env(env_p.read_text(encoding="utf-8", errors="ignore")) if env_p.is_file() else {}
    st = parse_settings_defaults(set_p.read_text(encoding="utf-8", errors="ignore"))
    reg = parse_registry(reg_p.read_text(encoding="utf-8", errors="ignore")) if reg_p.is_file() else set()

    py_files = [p for p in (root / "backend").rglob("*.py") if ".venv" not in str(p)]
    use = scan_usages(py_files)                       # 全部（含测试）
    use_prod = scan_usages(py_files, include_tests=False)  # [§54] 仅生产代码
    reads = scan_env_reads(py_files)
    helpers = scan_cfg_helpers(py_files)
    dyn_prefixes = scan_dynamic_prefixes(py_files)
    dyn_suffixes = scan_suffix_composed(py_files)

    defined = set(st)
    # [§54] 动态前缀读取的键**不是**死键（此前是工具盲点 → 误报）
    dyn_hits = {k: dynamic_prefix_matches(k, dyn_prefixes) for k in env}
    dyn_hits = {k: v for k, v in dyn_hits.items() if v}
    # [§54 修复 2] 后缀拼接读取（`X_PAPER`/`X_LIVE`）：同样不算死键
    suffix_hits = {}
    for k in env:
        for suf in dyn_suffixes:
            if k.endswith(suf) and len(k) > len(suf):
                suffix_hits[k] = suf
                break
    # 真·死配置：.env 有、settings 无、**生产代码**中任何地方都没出现过这个键名，
    # 且**不被任何动态前缀/后缀读取覆盖**（测试文件里出现不算使用）
    truly_dead = sorted(
        k for k in env
        if k not in defined and k not in use_prod
        and not dyn_hits.get(k) and not suffix_hits.get(k)
    )
    # [§54] 只在测试里出现 → 生产其实是死的（治理提示）
    test_only = sorted(
        k for k in env
        if k not in defined and k not in use_prod and k in use
        and not dyn_hits.get(k) and not suffix_hits.get(k)
    )
    # 直读配置：绕开 settings（治理缺口，但生效）
    direct_read = sorted(k for k in env if k not in defined and k in reads)
    # 安全类键：.env 值 ≠ 代码默认（归一化后比较）
    overrides = []
    for k in sorted(env):
        if not SAFETY_HINT.search(k) or k not in st:
            continue
        dflt, ln = st[k]
        if normalize(env[k]) != normalize(dflt):
            overrides.append({"key": k, "env": env[k], "code_default": dflt,
                              "line": ln, "direct_read": k in reads,
                              "uses": len(use.get(k, []))})
    findings = {
        "env_truly_dead": truly_dead,
        "env_dynamic_prefix_read": dyn_hits,
        "env_suffix_composed_read": suffix_hits,
        "env_used_only_in_tests": test_only,
        "env_direct_read": direct_read,
        "near_miss": detect_near_miss(truly_dead, set(use_prod) | defined),
        "settings_not_in_registry": sorted(k for k in defined if k not in reg),
        "settings_never_used": sorted(k for k in defined if len(use_prod.get(k, [])) <= 1),
        "safety_env_overrides": overrides,
    }
    falsy = []
    for p in py_files:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for lineno, kind, sev, snip in find_falsy_sites(text):
            ks = keys_in_snippet(snip)
            in_tests = _is_test_file(p)
            falsy.append({
                "file": f"{p.relative_to(ROOT)}:{lineno}", "kind": kind, "severity": sev,
                "snippet": snip, "keys": ks,
                "in_tests": in_tests,
                # [§55] 安全类判定排除测试文件（测试里的示例代码不是生产缺陷）
                "safety": (not in_tests) and any(SAFETY_HINT.search(k) for k in ks),
            })
    return {"env_n": len(env), "settings_n": len(st), "registry_n": len(reg),
            "findings": findings,
            "cfg_helpers": helpers,
            "cfg_helpers_dangerous": sum(1 for h in helpers if h["dangerous"]),
            "cfg_helpers_kinds": dict(Counter(h.get("kind") for h in helpers)),
            "falsy_sites": falsy,
            "falsy_high_n": sum(1 for f in falsy if f["severity"] == "HIGH"),
            "falsy_high_prod_n": sum(1 for f in falsy
                                     if f["severity"] == "HIGH" and not f["in_tests"]),
            "falsy_high_in_tests_n": sum(1 for f in falsy
                                         if f["severity"] == "HIGH" and f["in_tests"]),
            "falsy_high_safety_n": sum(1 for f in falsy
                                       if f["severity"] == "HIGH" and f["safety"]),
            "falsy_safety_n": sum(1 for f in falsy if f["safety"])}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=str(ROOT / "data" / "config_effective_audit.json"))
    args = ap.parse_args()
    rep = build_report()
    f = rep["findings"]

    print("=== 配置三层对齐 ===")
    print(f"  .env 键 {rep['env_n']} / settings 定义 {rep['settings_n']} / registry 登记 {rep['registry_n']}")

    print(f"\n=== A1. **真·死配置**：.env 有键、settings 无定义、全仓无 os.getenv 读取"
          f"（设了完全不生效）n={len(f['env_truly_dead'])} ===")
    print("  " + ", ".join(f["env_truly_dead"][:80]))

    print(f"\n=== A1b. **近名误配**（.env 键没人读，但与某个在读键高度相似 → 疑似拼写错误）"
          f"n={len(f['near_miss'])} ===")
    for nm in f["near_miss"][:25]:
        print(f"  .env `{nm['env_key']}`  ≈  代码读 `{nm['candidate']}`  (相似度 {nm['score']})")

    print(f"\n=== A1c. [§54] **动态前缀读取**（`os.getenv(f\"PREFIX_{{...}}\")`，"
          f"字面搜不到但**确实生效**）n={len(f['env_dynamic_prefix_read'])} ===")
    for k, pref in sorted(f["env_dynamic_prefix_read"].items()):
        print(f"  {k:<44} ← 前缀 `{pref}*`")

    print(f"\n=== A2. 直读配置：绕开 settings 直接 os.getenv（生效但无治理）"
          f"n={len(f['env_direct_read'])} ===")
    print("  " + ", ".join(f["env_direct_read"][:40]))

    print(f"\n=== B. 安全类键：.env 值 ≠ 代码默认（归一化后）n={len(f['safety_env_overrides'])} ===")
    for o in f["safety_env_overrides"][:40]:
        flag = "（值相等，噪声）" if normalize(o["env"]) == normalize(o["code_default"]) else ""
        print(f"  {o['key']:<44} .env={o['env']!r:<14} 默认={o['code_default']!r:<14} {flag}")

    print(f"\n=== C. settings 定义但全仓仅此处出现（疑似死键）n={len(f['settings_never_used'])} ===")
    print("  " + ", ".join(f["settings_never_used"][:60]))

    print(f"\n=== D. 未登记 env_registry n={len(f['settings_not_in_registry'])} ===")
    print("  " + ", ".join(f["settings_not_in_registry"][:60]))

    print(f"\n=== E. falsy 吞值站点 ===")
    print(f"  全部 {len(rep['falsy_sites'])} 处；**HIGH（getattr(settings) or X，会吞 0/False）"
          f" {rep['falsy_high_n']} 处**，其中涉安全键 {rep['falsy_high_safety_n']} 处")
    print("  HIGH 明细（前 40）：")
    for s in rep["falsy_sites"]:
        if s["severity"] != "HIGH":
            continue
        flag = "⚠安全" if s["safety"] else "     "
        print(f"    {flag} {s['file']}: {s['snippet'][:95]}")
    print(f"\n=== F. 配置读取助手 `_cfg_*`/`_env_*`：共 {len(rep['cfg_helpers'])} 个，"
          f"其中含 `or default` {rep['cfg_helpers_dangerous']} 个 ===")
    print("  （注：仅当左值为已解析的 int/float/bool 时才有害；读 os.getenv 字符串的 or 无害）")
    for h in rep["cfg_helpers"]:
        if not h["dangerous"]:
            continue
        print(f"    ⚠ {h['file']:<56}{h['fn']}")
        print(f"        {h['body'][:110]}")
    seen = set()
    for s in rep["falsy_sites"]:
        if not s["safety"]:
            continue
        key = (s["file"], s["kind"])
        if key in seen:
            continue
        seen.add(key)
        print(f"  [{s['kind']}] {s['file']}: {s['snippet'][:90]}")

    out = Path(args.json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
