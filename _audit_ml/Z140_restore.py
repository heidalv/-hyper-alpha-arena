from pathlib import Path
p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
bak = p.with_suffix(".md.bakinv")
p.write_text(bak.read_text(encoding="utf-8"), encoding="utf-8")
bak.unlink()
print("已还原")
