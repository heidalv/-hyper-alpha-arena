"""h570 — `h464_chain._fresh_verdict()` 的**终局不过期**改动的离线验证（R69）。

背景（R69）：隔离闸可能把 ③ 的链改期到次日（例如为避开"薄夜 + 参数自身 −8% 腿量"
把有益参数顶到 60/h 硬地板之下）。触发时 ② 的判决年龄已 >2h，旧逻辑判为"不新鲜"
⇒ 链自己重跑 `--trial h463 --judge`，而 `since` 未变、`now` 已是次日
⇒ **② 的干净 12h 白天窗被覆盖成 24h 昼夜混合窗**（R68：昼夜对 `legs_per_trip` 影响 ≈2.4×）
⇒ ② 的验收记录失真。改为：**终局判决无条件复用**，只有非终局才要求新鲜度。

本用例通过 `H464_VERDICT_PATH` 钩子把判定产物指向临时文件
⇒ **不触碰生产产物**（R41/R57 纪律），并在结尾断言生产文件未被改动。

用法：python scripts/h570_verify_fresh_verdict.py
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

PROD_VERDICT = ROOT / "research_l1" / "out" / "h463_verdict.json"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "(absent)"


def _load_chain(verdict_path: Path):
    """在**指定判定路径**下重新导入链模块（钩子必须在 import 时生效）。"""
    os.environ["H464_VERDICT_PATH"] = str(verdict_path)
    spec = importlib.util.spec_from_file_location(
        "h464_chain_t", ROOT / "scripts" / "h464_chain.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)  # type: ignore[union-attr]
    return m


def main() -> int:
    prod_before = _sha(PROD_VERDICT)
    cases: list[tuple[str, dict, float, bool]] = [
        # (名称, verdict 内容, 文件年龄(小时), 期望是否复用)
        ("终局PASS + 12h 龄", {"verdict": "PASS", "hours": 12.0}, 12.0, True),
        ("终局ROLLBACK + 30h 龄", {"verdict": "ROLLBACK", "hours": 24.0}, 30.0, True),
        ("非终局INCONCLUSIVE + 12h 龄", {"verdict": "INCONCLUSIVE", "hours": 12.0}, 12.0, False),
        ("非终局INCONCLUSIVE + 1h 龄 + 12h 窗",
         {"verdict": "INCONCLUSIVE", "hours": 12.0}, 1.0, True),
        ("非终局 + 1h 龄 + 仅 5h 窗", {"verdict": "INCONCLUSIVE", "hours": 5.0}, 1.0, False),
        # 缺 `verdict` 字段但新鲜的产物：**沿用旧行为（复用）**——本用例第一次跑时我
        # 期望 False 而失败，核对后判定是**期望写错**：该路径我并未改动，且复用比
        # 重跑更安全（重跑会把窗口覆盖变长）。链自身的安全性由 `_gate` 的终局判定保证。
        ("无 verdict 字段 + 1h 龄（沿用旧行为）", {"hours": 12.0}, 1.0, True),
    ]
    ok = True
    with tempfile.TemporaryDirectory() as td:
        vp = Path(td) / "h463_verdict.json"
        for name, payload, age_h, want in cases:
            vp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            _old = time.time() - age_h * 3600.0
            os.utime(vp, (_old, _old))
            m = _load_chain(vp)
            got = m._fresh_verdict() is not None
            flag = "✓" if got == want else "✗"
            if got != want:
                ok = False
            print(f"  {flag} {name}: 复用={got}（期望 {want}）")
        # 文件不存在
        vp.unlink()
        m = _load_chain(vp)
        got = m._fresh_verdict() is not None
        flag = "✓" if got is False else "✗"
        if got is not False:
            ok = False
        print(f"  {flag} 产物不存在: 复用={got}（期望 False）")

    prod_after = _sha(PROD_VERDICT)
    same = prod_before == prod_after
    print(f"  {'✓' if same else '✗'} 生产产物未被触碰（sha256 {prod_before[:12]}…）")
    os.environ.pop("H464_VERDICT_PATH", None)
    print("=" * 72)
    print("✓ 全部通过" if (ok and same) else "✗ 有失败")
    return 0 if (ok and same) else 1


if __name__ == "__main__":
    raise SystemExit(main())
