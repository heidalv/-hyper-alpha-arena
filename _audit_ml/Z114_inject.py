import sys
from pathlib import Path
f = Path(r"D:\001Alpha\Hyper-Alpha-Arena\backend\services\full_auto\master_execution.py")
bak = f.with_suffix(".py.bakaudit2")
bak.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
t = f.read_text(encoding="utf-8")
anchor = '                    # [§56] 微仓全平不消耗每日开单配额'
assert anchor in t, "锚点未找到"
t = t.replace(anchor, '                    _bump_live_open_quota()\n' + anchor, 1)
f.write_text(t, encoding="utf-8")
print("已注入一处平仓侧调用（用于验证护栏会变红）")
