import io
p = r'D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md'
s = io.open(p, encoding='utf-8').read()
old = "`backend/scripts/audit_llm_direction_edge.py` 与 `audit_factor_library_health.py` 可周期性重跑作回归监控。"
new = "`backend/scripts/audit_llm_direction_edge.py`、`audit_factor_library_health.py`、`seed_factor_families.py`\n可周期性重跑作回归监控。"
if old in s:
    s = s.replace(old, new)
    io.open(p, 'w', encoding='utf-8').write(s)
    print("footer updated")
else:
    print("old not found; tail:", repr(s[-160:]))
