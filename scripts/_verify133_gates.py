"""轮133 验证：strategy_detached / long_template_source_block 两个大头是否被止住。"""
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

print("等待活链路跑几轮（最多 240s）…")
audit = Path("data/midlong_direction_audit.jsonl")
mark = time.time()


def dist(since_iso: str) -> Counter:
    c: Counter = Counter()
    if not audit.exists():
        return c
    for ln in audit.read_text(encoding="utf-8", errors="replace").splitlines()[-40000:]:
        try:
            d = json.loads(ln)
        except Exception:  # noqa: BLE001
            continue
        ts = str(d.get("ts") or "")[:19].replace("T", " ")
        if ts < since_iso:
            continue
        r = str(d.get("reason") or "")
        if r.startswith("eval_false") or r in ("strategy_detached",):
            code = r.split(":", 1)[1].split()[0] if r.startswith("eval_false") and ":" in r else r
            c[code] += 1
    return c


restart_iso = "2026-09-20 12:35"
for _ in range(24):
    time.sleep(10)
    d = dist(restart_iso)
    if sum(d.values()) >= 3:
        break

print(f"\n=== 重启后（{restart_iso} 起）拦截码分布 ===")
d = dist(restart_iso)
if not d:
    print("   （窗口内暂无新的拦截记录）")
for k, v in d.most_common(10):
    print(f"   {v:>4}  {k}")

print("\n=== 对照组：重启前 24h ===")
d0 = dist("2026-09-19 12:30")
for k, v in d0.most_common(6):
    print(f"   {v:>4}  {k}")

print("\n=== 上游跳过是否生效（日志）===")
bl = Path("logs/backend.log").read_text(encoding="utf-8", errors="replace")
hits = [ln for ln in bl.splitlines() if "模板族上游跳过" in ln or "补建后仍无" in ln]
print(f"   命中 {len(hits)} 行")
for ln in hits[-5:]:
    print("   ", ln[:19], ln.split(" - ", 1)[-1][:140])
det = [ln for ln in bl.splitlines() if "策略对象无效或已 detach" in ln and ln[:19] >= restart_iso]
print(f"   重启后 strategy_detached 警告 = {len(det)} 行（修复前 24h 94 次）")
for ln in det[-4:]:
    print("   ", ln[:19], ln.split(" - ", 1)[-1][:120])
