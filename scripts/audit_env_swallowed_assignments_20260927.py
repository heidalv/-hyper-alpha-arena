# -*- coding: utf-8 -*-
"""`.env` 赋值被吞进注释行的审计（[新目标 R4 附带发现]，2026-09-27）。

## 现象
`test_thesis_inv_semantics_20260916.py::test_deployed_env_values` 变红：
`MIDLONG_THESIS_INV_REQUIRE_CLOSE` 在 `.env` 里**根本没有被解析出来**。
逐行看才发现 `.env` 有一批**注释行内部粘着赋值**，例如第 1975 行（单行 390 字符）：

    # …（回滚：MIDLONG_THESIS_INV_REQUIRE_CLOSE=false（回到「触碰即平」）。MIDLONG_THESIS_INV_REQUIRE_CLOSE=true

整行以 `#` 开头 ⇒ dotenv 当注释 ⇒ **该键静默失效，代码回落到默认值**。
行内还残留 `\\ue1ee`/`\\ue218` 这类**私用区码点**，说明这批文本经历过一次有损编码转换
（我的两次改动都是纯 ASCII 追加/替换，不可能产生私用区码点 ⇒ 属于**历史遗留损坏**，非本次引入）。

## 判据（为什么取"行内最后一次赋值"）
本仓注释约定是「现置 X；回滚：KEY=Y」，因此同一行里 KEY 的最后一次出现才是**生效值**。
该启发式在唯一有真值可校的键上成立：提取出 `MIDLONG_THESIS_INV_REQUIRE_CLOSE=true`，
而回归测试断言的正是 `true`。

## 输出
line / key(可能被前一个乱码字符吃掉首字母，故同时给"后缀匹配到的真实键") /
intended（最后一次赋值）/ code_default（代码里 `os.getenv(key, default)`）/ verdict。
verdict=BEHAVIOUR_DRIFT 表示"生效值 ≠ 代码默认"，这类才是真正影响行为的；
verdict=NEUTRAL 表示与默认一致（补回只是修文档/存在性检查）。

用法：.venv\\Scripts\\python.exe scripts\\audit_env_swallowed_assignments_20260927.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
TOKEN = re.compile(r"([A-Z][A-Z0-9_]{3,})=([A-Za-z0-9_.:+-]+)")
GETENV = re.compile(
    r"(?:environ\.get|os\.getenv|getenv)\(\s*[\"']([A-Z0-9_]{3,})[\"']\s*(?:,\s*([^)\n]{0,40}))?"
)


def code_defaults() -> dict:
    """扫 backend/ + scripts/ 收集 key → [默认值字面量]。"""
    out: dict = {}
    for base in ("backend", "scripts"):
        for p in (ROOT / base).rglob("*.py"):
            try:
                if p.stat().st_size > 2_000_000:
                    continue
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for m in GETENV.finditer(text):
                key, raw = m.group(1), (m.group(2) or "").strip()
                out.setdefault(key, []).append(raw)
    return out


def normalise(raw: str) -> str:
    s = (raw or "").strip().strip("\"'").strip()
    if s.lower() in ("true", "false"):
        return s.lower()
    return s


def main() -> int:
    from dotenv import dotenv_values

    vals = dotenv_values(str(ENV))
    defaults = code_defaults()
    lines = ENV.read_text(encoding="utf-8", errors="replace").splitlines()

    rows = []
    for i, line in enumerate(lines, 1):
        if ASSIGN.match(line) or not line.lstrip().startswith("#"):
            continue
        toks = TOKEN.findall(line)
        if not toks:
            continue
        # 同一行同一个键可能出现多次（回滚说明 + 生效值）⇒ 取最后一次
        last_by_key = {}
        for k, v in toks:
            last_by_key[k] = v
        for k, v in last_by_key.items():
            if k in vals:
                continue  # 别处已正常赋值 ⇒ 注释里的提及无害
            # 乱码可能吃掉首字母（IDLONG_… 实为 MIDLONG_…）⇒ 用后缀匹配已知键
            real = k
            if k not in defaults:
                cands = [dk for dk in defaults if dk.endswith(k)]
                if len(cands) == 1:
                    real = cands[0]
            # 真实键也检查一遍：若真实键已在 vals 里则不缺
            if real in vals:
                continue
            if real not in defaults:
                continue  # 代码里根本没读这个 key ⇒ 不是"被吞的开关"，先不报
            dv = defaults[real]
            dset = {normalise(x) for x in dv if x}
            intended = normalise(v)
            if not dset:
                verdict = "NO_DEFAULT_IN_CODE"
            elif intended in dset:
                verdict = "NEUTRAL"
            else:
                verdict = "BEHAVIOUR_DRIFT"
            rows.append((i, real, intended, "|".join(sorted(dset)) or "-", verdict, len(line)))

    drift = [r for r in rows if r[4] == "BEHAVIOUR_DRIFT"]
    print("=" * 120)
    print(f".env 被吞赋值的键（代码确实读取、且 .env 未解析到）= {len(rows)}；"
          f"其中与代码默认不一致（真正影响行为）= {len(drift)}")
    print("=" * 120)
    print(f"{'行':>6} {'键':<44}{'应为':<26}{'代码默认':<26}{'判定':<17}")
    for i, k, intended, dflt, verdict, _ln in sorted(rows, key=lambda r: (r[4] != "BEHAVIOUR_DRIFT", r[0])):
        print(f"{i:>6} {k:<44}{intended:<26}{dflt:<26}{verdict:<17}")
    print()
    print("说明：BEHAVIOUR_DRIFT 是真正在跑的配置与文档不一致的键 —— 必须逐个人工确认后补回；")
    print("      NEUTRAL 只是补回文档/存在性语义（代码默认值已等效），可安全补齐。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
