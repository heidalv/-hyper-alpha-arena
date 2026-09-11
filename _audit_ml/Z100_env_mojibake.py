import re
from pathlib import Path
p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\.env")
lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
bad = [(i, l) for i, l in enumerate(lines, 1) if re.search(r"\?{5,}", l)]
print(f".env 总行 {len(lines)}；疑似编码损坏（连续 ≥5 个 ?）行数 {len(bad)}")
for i, l in bad[:10]:
    print(f"   {i}: {l[:110]}")
