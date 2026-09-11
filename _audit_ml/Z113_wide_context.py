from pathlib import Path
p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\backend\services\full_auto\master_execution.py")
lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
for target in (2166, 1965, 2555, 2619):
    print(f"\n===== 站点 {target} 上下文 =====")
    for j in range(target - 14, target + 4):
        if 0 <= j < len(lines):
            mark = ">>" if j + 1 == target else "  "
            s = lines[j].rstrip()
            if s.strip():
                print(f"  {mark} {j+1}: {s[:140]}")
