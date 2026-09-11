from pathlib import Path
p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
bak = p.with_suffix(".md.bakinv")
bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
t = p.read_text(encoding="utf-8")
t = t.replace("条目 **42** 条（已合并", "条目 **40** 条（已合并", 1)
t = t.replace("`test_audit_rotation_reader_20260910.py` | ✅ 已修（§57.3）",
              "`test_does_not_exist_zzz.py` | ✅ 已修（§57.3）", 1)
p.write_text(t, encoding="utf-8")
print("已注入两处漂移（统计数字错 + 证据测试不存在）")
