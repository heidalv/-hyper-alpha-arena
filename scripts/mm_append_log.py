import io, os, sys

target = r"D:\001Alpha\Hyper-Alpha-Arena\_全链路打通修复轮_20260911.md"
src = sys.argv[1] if len(sys.argv) > 1 else r"D:\001Alpha\Hyper-Alpha-Arena\logs\_append.md"
with io.open(src, "r", encoding="utf-8") as f:
    add = f.read()
before = os.path.getsize(target)
with io.open(target, "a", encoding="utf-8") as f:
    f.write(add)
print(f"appended {len(add)} chars; {before} -> {os.path.getsize(target)} bytes")
