import pathlib
p = pathlib.Path("backend/tests/unit/test_brain_debate_wiring_20260920.py")
s = p.read_text(encoding="utf-8")
rep = [
 ('    for cn in ("日内", "中期", "长期趋势"):\n        assert cn in instr, f"周期契约里缺 {cn}"',
  '    for cn in ("日内", "长期趋势"):   # [轮159] 两周期口径，中期档已删\n        assert cn in instr, f"周期契约里缺 {cn}"'),
 ('    """模型不给 horizons ⇒ 三档标 unknown（不猜、也不崩）。"""\n    hz = BD._parse_horizons(\'{"argument":"x","confidence":0.6}\')\n    for k in ("intraday", "swing", "trend"):',
  '    """模型不给 horizons ⇒ 每档标 unknown（不猜、也不崩）。"""\n    hz = BD._parse_horizons(\'{"argument":"x","confidence":0.6}\')\n    for k in ("intraday", "trend"):'),
 ('    assert hz2["swing"]["stance"] == "unknown", "未给出的周期必须显式 unknown"',
  '    assert set(hz2.keys()) == {"intraday", "trend"}, "只应有两档（中期档已删）"'),
]
for a, b in rep:
    if a not in s:
        print("!! 未命中：", a.splitlines()[0][:60])
    s = s.replace(a, b)
p.write_text(s, encoding="utf-8")
print("patched2")
