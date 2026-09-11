"""Z152: 收尾 env_registry —— ①补登记 FACTOR_TRIALS_REGISTRY_PATH ②去掉 KNOWN_FLAGS 里的重复项。"""
import re
from pathlib import Path
p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\backend\config\env_registry.py")
src = p.read_text(encoding="utf-8")
start = src.index("KNOWN_FLAGS: frozenset[str] = frozenset({")
end = src.index("\n})", start)
block = src[start:end]
lines = block.split("\n")
seen, out, removed = set(), [], []
for ln in lines:
    m = re.match(r'\s*"([A-Z][A-Z0-9_]{2,})",', ln)
    if not m:
        out.append(ln); continue
    k = m.group(1)
    if k in seen:
        removed.append(k); continue
    seen.add(k); out.append(ln)
# 补登记 FACTOR_TRIALS_REGISTRY_PATH（生产代码 trials_registry.py:46 确实读取）
if "FACTOR_TRIALS_REGISTRY_PATH" not in seen:
    out.insert(1, '    "FACTOR_TRIALS_REGISTRY_PATH",  # [§63] trials_registry 的登记簿路径覆盖（生产读取）')
new_block = "\n".join(out)
src = src[:start] + new_block + src[end:]
p.write_text(src, encoding="utf-8")
print(f"去掉重复项 {len(removed)}: {removed}")
print("已补登记 FACTOR_TRIALS_REGISTRY_PATH")
