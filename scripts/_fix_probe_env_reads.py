# -*- coding: utf-8 -*-
"""轮117：去掉探针脚本里的**动态** os.getenv(变量) —— 它会被 env 治理扫描器
当成"读了新键"，把 P2-14 棘轮从 281 顶到 283（实测）。

探针输出已存成 reports/_probe116.txt / _probe117b.txt，这里改成读 .env 文本。
"""
import io
import re

PATCH = {
    "scripts/_probe116.py": (
        'for _k in ("LAYER_BUDGET_SCALP", "LAYER_BUDGET_TREND", "LAYER_BUDGET_SWING",\n'
        '           "TIER_SHORT_BUDGET", "TIER_SHORT_MAX_MARGIN",\n'
        '           "TIER_MID_BUDGET", "TIER_MID_MAX_MARGIN",\n'
        '           "TIER_LONG_BUDGET", "TIER_LONG_MAX_MARGIN",\n'
        '           "SCALP_OPEN_DISABLED", "SCALP_SHADOW_MODE"):\n'
        '    w(f"     {_k:24s} = {_env_map().get(_k)!r}")\n',
        'for _k in ("LAYER_BUDGET_SCALP", "LAYER_BUDGET_TREND", "LAYER_BUDGET_SWING",\n'
        '           "TIER_SHORT_BUDGET", "TIER_SHORT_MAX_MARGIN",\n'
        '           "TIER_MID_BUDGET", "TIER_MID_MAX_MARGIN",\n'
        '           "TIER_LONG_BUDGET", "TIER_LONG_MAX_MARGIN",\n'
        '           "SCALP_OPEN_DISABLED", "SCALP_SHADOW_MODE"):\n'
        '    w(f"     {_k:24s} = {_ENV_TEXT.get(_k)!r}")\n',
    ),
    "scripts/_probe117b.py": (
        'for k in ("MIDLONG_BRAIN_OPEN_MARGIN_PCT", "MIDLONG_MIN_SIZE_MULT", "MIDLONG_RISK_PCT",\n'
        '          "MIDLONG_MAX_SL_PCT_MID", "V5_SCALP_MIN_CONFIDENCE"):\n'
        '    w(f"   {k:32s} .env={os.getenv(k)!r}")\n',
        'for k in ("MIDLONG_BRAIN_OPEN_MARGIN_PCT", "MIDLONG_MIN_SIZE_MULT", "MIDLONG_RISK_PCT",\n'
        '          "MIDLONG_MAX_SL_PCT_MID", "V5_SCALP_MIN_CONFIDENCE"):\n'
        '    w(f"   {k:32s} .env={_ENV_TEXT.get(k)!r}")\n',
    ),
}

HELPER = (
    "\n\ndef _load_env_text():\n"
    "    _d = {}\n"
    "    try:\n"
    "        for _ln in io.open('.env', encoding='utf-8', errors='replace'):\n"
    "            _s = _ln.strip()\n"
    "            if _s and not _s.startswith('#') and '=' in _s:\n"
    "                _k, _v = _s.split('=', 1)\n"
    "                _d[_k.strip()] = _v.strip()\n"
    "    except Exception:\n"
    "        pass\n"
    "    return _d\n\n\n"
    "_ENV_TEXT = _load_env_text()\n"
)

for path, (old, new) in PATCH.items():
    raw = io.open(path, encoding="utf-8", errors="surrogateescape", newline="").read()
    if old not in raw:
        print(f"[!] 未匹配（可能已改过）: {path}")
        continue
    raw = raw.replace(old, new, 1)
    if "_ENV_TEXT = _load_env_text()" not in raw:
        # 插到第一次出现 OUT = io.open(...) 之前
        i = raw.index("OUT = io.open(")
        raw = raw[:i] + HELPER.lstrip("\n") + "\n" + raw[i:]
    io.open(path, "w", encoding="utf-8", errors="surrogateescape", newline="").write(raw)
    print(f"[OK] 已改: {path}")
