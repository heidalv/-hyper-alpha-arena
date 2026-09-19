# -*- coding: utf-8 -*-
"""轮122：把扫描测试里的策略 stub 改成真实形状（status/account_id）。

新规则要求策略**能绑定到本会话/本账户**（否则执行层会 strategy_detached，
ZEC 就是这样无限循环的）。旧的 `object()` stub 没有 status/account_id ⇒ 全被跳过，
测试因此变红 —— 这是测试变严，不是回归。
"""
import io

STUB_DEF = '''

class _ActiveStrat:
    """真实形状的 active 策略（account 与会话一致 ⇒ 可绑定）。"""

    def __init__(self, status="active", account_id=14):
        self.status = status
        self.account_id = account_id
        self.strategy_id = "tpl_test_active"

'''

PATCHES = [
    ("backend/tests/unit/test_mid_sweep_zombie_candidate_20260919.py",
     [('            return None if sym == "ZEC" else object()',
       '            if sym == "ZEC":\n                return None\n            return _ActiveStrat()')]),
    ("backend/tests/unit/test_probe_and_symbol_lock_20260919.py",
     [('            return object()', '            return _ActiveStrat()')]),
    ("backend/tests/unit/test_ai_mid_candidate_tradability_20260919.py", []),
]

for path, subs in PATCHES:
    raw = io.open(path, encoding="utf-8", errors="surrogateescape", newline="").read()
    changed = False
    for old, new in subs:
        if old in raw:
            raw = raw.replace(old, new, 1)
            changed = True
    if changed and "_ActiveStrat" in raw and "class _ActiveStrat" not in raw:
        # 插到第一个 def test_ 之前
        i = raw.index("def test_")
        raw = raw[:i] + STUB_DEF.lstrip("\n") + "\n\n" + raw[i:]
    if changed:
        io.open(path, "w", encoding="utf-8", errors="surrogateescape", newline="").write(raw)
        print("[OK]", path)
    else:
        print("[--] 无需改:", path)
