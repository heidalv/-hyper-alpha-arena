"""Z149: 把「代码确实会读」的 .env 键批量登记到 KNOWN_FLAGS。

判定「确实会读」= 字面读取 ∪ 动态前缀读取 ∪ 后缀拼接读取 ∪ settings 已定义。
剩下的（.env 有值、代码任何方式都不读）= 真·死键，让注册表把它们**精确**报出来（与 §54 对齐）。
"""
from __future__ import annotations
import re, sys
from pathlib import Path

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT/"backend"/"scripts"))
import audit_config_effective as ace

reg_path = ROOT / "backend" / "config" / "env_registry.py"
src = reg_path.read_text(encoding="utf-8")
known = set(re.findall(r'"([A-Z][A-Z0-9_]{2,})"', src))
env = ace.parse_env((ROOT/".env").read_text(encoding="utf-8", errors="ignore"))
st = ace.parse_settings_defaults((ROOT/"backend"/"config"/"settings.py").read_text(encoding="utf-8", errors="ignore"))

py_files = [p for p in (ROOT/"backend").rglob("*.py") if ".venv" not in str(p) and "_ptmp" not in str(p)]
reads = set(ace.scan_env_reads(py_files)) | set(ace.scan_usages(py_files, include_tests=False))
prefixes = ace.scan_dynamic_prefixes(py_files)
suffixes = ace.scan_suffix_composed(py_files)

to_add = []
for k in sorted(env):
    if k in known:
        continue
    dyn = bool(ace.dynamic_prefix_matches(k, prefixes)) or any(k.endswith(s) and len(k) > len(s) for s in suffixes)
    if k in reads or dyn or k in st:
        to_add.append(k)

print(f".env 键 {len(env)}；已登记 {len(known)}；本次补登记 {len(to_add)}")
print("补登记清单（前 30）:", ", ".join(to_add[:30]))
residual = [k for k in sorted(env) if k not in known and k not in to_add]
print(f"\n剩余未登记（=代码任何方式都不读 ⇒ 真死键候选）{len(residual)}:")
for k in residual:
    print("   ", k)

block = "\n    # ── [§63 批量登记 2026-09-10] 代码确实会读（字面/动态前缀/后缀拼接/settings）的 .env 键 ──\n"
block += "    # 保留在名单外的即是「.env 设了但代码任何方式都不读」的真·死键（注册表会精确报出）。\n"
for k in to_add:
    block += f'    "{k}",\n'
anchor = "KNOWN_FLAGS: frozenset[str] = frozenset({\n"
src = src.replace(anchor, anchor + block, 1)
reg_path.write_text(src, encoding="utf-8")
print("\n已写入 env_registry.py")
