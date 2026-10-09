import pathlib
p = pathlib.Path("backend/tests/unit/test_brain_debate_wiring_20260920.py")
s = p.read_text(encoding="utf-8")
rep = [
 ('    # [轮130 周期化] 三个周期都必须拿到证据（空手辩论 = 又一次"装饰"）\n    for k in ("intraday", "swing", "trend"):',
  '    # [轮159 2026-09-21] 两周期口径（日内/长期趋势）——原为三档，中期档已删（见 BD.HORIZONS 注释）\n    for k in ("intraday", "trend"):'),
 ('    assert by_hz["swing"], "中期必须有证据（4h结构/收益/因子）"',
  '    assert by_hz["intraday"], "日内必须有证据（1h/4h 结构、量价、因子路线）"'),
 ('def test_three_horizons_declared_with_scope():\n    keys = [h[0] for h in BD.HORIZONS]\n    assert keys == ["intraday", "swing", "trend"]\n    assert BD.HORIZON_CN == {"intraday": "日内", "swing": "中期", "trend": "长期趋势"}',
  'def test_two_horizons_declared_with_scope():\n    """[轮159 2026-09-21] 两周期口径：日内 / 长期趋势（与 config/cycle_semantics.py 一致）。"""\n    keys = [h[0] for h in BD.HORIZONS]\n    assert keys == ["intraday", "trend"], "周期档只允许两档（用户口径：日内 + 长期趋势）"\n    assert BD.HORIZON_CN == {"intraday": "日内", "trend": "长期趋势"}'),
]
for a, b in rep:
    if a not in s:
        print("!! 未命中：", a.splitlines()[0][:60])
    s = s.replace(a, b)
p.write_text(s, encoding="utf-8")
print("patched")
