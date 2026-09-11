# -*- coding: utf-8 -*-
"""审计工具：配置**运行时生效性**探针（目标第 1 轮 B）。

静态扫描（`audit_config_effective.py`）只能看写法；本工具直接问：
「把 KEY 设成边界值（0 / false / 哨兵串）后，`settings.KEY` 到底变成什么？」

在**子进程**里逐键 `os.environ[K]=edge` → `importlib.reload(settings)` → 读值，
与「按代码默认值类型推断的期望值」比较。不一致即判为**吞值/失效**。

已知实例：`MIDLONG_MAX_OPEN_POSITIONS` 走 `int(getenv(...) or "4")`，
显式设 0 会被吞成 4（§38.9/2）。

用法：
  .venv\\Scripts\\python.exe backend/scripts/audit_config_runtime_probe.py [--all]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from audit_config_effective import (  # noqa: E402
    SAFETY_HINT,
    normalize,
    parse_env,
    parse_settings_defaults,
)

# 只在「交易/风控/闸门」相关键上做探针（全量 175 个键会拖慢且噪声大）
PROBE_HINT = re.compile(
    r"^(MIDLONG_|PAPER_|EXIT_POLICY_|RISK_|TREND_|SWING_|LONG_|POSITION_|TP_|SL_|"
    r"AUTO_COIN_|TIER_|LIVE_|MLTO_|FUSION_|FACTOR_ROUTE)",
)

CHILD = r'''
import importlib, json, os, sys
sys.path.insert(0, ROOT)
import backend.config.settings as S
keys = json.loads(os.environ["PROBE_KEYS"])
edges = json.loads(os.environ["PROBE_EDGES"])
out = {}
for k in keys:
    os.environ[k] = edges[k]
    try:
        importlib.reload(S)
        v = getattr(S, k, None)
        out[k] = {"value": (v if isinstance(v, (int, float, bool, str, type(None))) else str(v)),
                  "type": type(v).__name__}
    except Exception as e:
        out[k] = {"value": None, "type": "ERR", "err": str(e)[:80]}
    finally:
        os.environ.pop(k, None)
print("<<<PROBE_JSON>>>" + json.dumps(out, ensure_ascii=False))
'''


def choose_edge(default_expr: str):
    """按代码默认值推断一个「边界值」与期望结果。"""
    d = normalize(default_expr)
    if d in ("true", "false"):
        edge = "false" if d == "true" else "true"
        return edge, (edge == "true")
    if re.fullmatch(r"-?\d+", d):
        edge = "0" if d != "0" else "1"
        return edge, int(edge)
    if re.fullmatch(r"-?\d*\.\d+", d):
        edge = "0" if d != "0" else "1"
        return edge, float(edge)
    return "ZZZPROBE", "ZZZPROBE"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="不限于风控相关键")
    args = ap.parse_args()

    env = parse_env((ROOT / ".env").read_text(encoding="utf-8", errors="ignore"))
    st = parse_settings_defaults(
        (ROOT / "backend" / "config" / "settings.py").read_text(encoding="utf-8", errors="ignore")
    )
    keys, edges, expect = [], {}, {}
    for k, (dflt, _ln) in sorted(st.items()):
        if not args.all and not PROBE_HINT.match(k):
            continue
        if not SAFETY_HINT.search(k) and not PROBE_HINT.match(k):
            continue
        e, exp = choose_edge(dflt)
        keys.append(k)
        edges[k] = e
        expect[k] = exp

    print(f"探针键数 = {len(keys)}（子进程重载 settings）")
    child = ROOT / "backend" / "scripts" / "_probe_child.py"
    child.write_text("ROOT = r'%s'\n" % str(ROOT) + CHILD, encoding="utf-8")
    env_vars = dict(os.environ)
    env_vars["PROBE_KEYS"] = json.dumps(keys)
    env_vars["PROBE_EDGES"] = json.dumps(edges)
    env_vars["PYTHONIOENCODING"] = "utf-8"
    res = subprocess.run([sys.executable, str(child)], capture_output=True, text=True,
                         encoding="utf-8", errors="ignore", env=env_vars, cwd=str(ROOT),
                         timeout=600)
    out = {}
    for line in (res.stdout or "").splitlines():
        if line.startswith("<<<PROBE_JSON>>>"):
            out = json.loads(line[len("<<<PROBE_JSON>>>"):])
    if not out:
        print("探针失败：", (res.stdout or "")[-500:], (res.stderr or "")[-500:])
        return 1

    bad, ok = [], []
    for k in keys:
        got = out.get(k) or {}
        gv = got.get("value")
        exp = expect[k]
        same = (str(gv).lower() == str(exp).lower())
        (ok if same else bad).append((k, edges[k], exp, gv, got.get("type")))

    print(f"\n=== 生效正常 {len(ok)} 个 ===")
    print(f"\n=== ⚠ 未生效 / 被吞值 {len(bad)} 个 ===")
    print(f"  {'KEY':<46}{'设成':<10}{'期望':<10}{'实际':<12}类型")
    for k, e, exp, gv, t in bad:
        print(f"  {k:<46}{e:<10}{str(exp):<10}{str(gv):<12}{t}")

    out_p = ROOT / "data" / "config_runtime_probe.json"
    out_p.write_text(json.dumps({"n": len(keys), "bad": [
        {"key": k, "edge": e, "expect": str(exp), "got": str(gv), "type": t}
        for k, e, exp, gv, t in bad]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {out_p}")
    try:
        child.unlink()
    except Exception:
        pass
    print(f"（.env 中这些键的现值：{ {k: env.get(k) for k, *_ in bad[:12]} }）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
