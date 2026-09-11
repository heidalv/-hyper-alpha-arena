from pathlib import Path
from collections import Counter
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
pats = {
    "拦截(期望值不足)": "期望值不足拦截",
    "影子·未校准放行": "[影子·未校准放行]",
    "影子·全局关": "[影子·全局关]",
    "EV 闸总数": "[MidLongEvGate]",
}
c = Counter()
files = sorted([p for p in (ROOT/"logs").glob("*.log") if p.stat().st_size > 200_000], key=lambda p:-p.stat().st_size)
seen = set()
for p in files:
    try:
        txt = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        continue
    for line in txt.splitlines():
        if "[MidLongEvGate]" not in line:
            continue
        # 去重（同一行可能出现在多个日志文件里）
        key = line.strip()[:160]
        if key in seen:
            continue
        seen.add(key)
        for name, tag in pats.items():
            if tag in line:
                c[name] += 1
for k, v in c.most_common():
    print(f"  {v:>7}  {k}")
