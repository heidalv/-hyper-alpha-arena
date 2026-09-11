from pathlib import Path
p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\backend\services\full_auto\master_execution.py")
lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
for i, l in enumerate(lines, 1):
    if "_bump_live_open_quota()" in l and "def " not in l:
        print(f"  {i}: {l.strip()}")
        print(f"       上文: {lines[i-3].strip()[:100]}")
        print(f"       下文: {lines[i].strip()[:100] if i < len(lines) else ''}")
