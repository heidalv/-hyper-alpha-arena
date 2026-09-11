from pathlib import Path
f = Path(r"D:\001Alpha\Hyper-Alpha-Arena\backend\services\full_auto\master_execution.py")
bak = f.with_suffix(".py.bakaudit2")
f.write_text(bak.read_text(encoding="utf-8"), encoding="utf-8")
bak.unlink()
print("已还原")
