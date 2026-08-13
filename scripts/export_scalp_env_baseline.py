"""导出 scalp 相关 env 基线快照（M0 交付）。

只导出非密钥的 SCALP_* / FEATURE_FACTOR_LABELS_ENABLED / SCALP_META_* /
PB_SCALP_* 键，避免把 API Key 等敏感值写进配置文件。

用法（仓库根目录）：
    python scripts/export_scalp_env_baseline.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIXES = ("SCALP_", "FEATURE_FACTOR_LABELS_ENABLED", "SCALP_META_", "PB_SCALP_")
SECRET_HINTS = ("KEY", "SECRET", "PASSWORD", "TOKEN", "API")


def _load_env_file(path: Path) -> dict:
    out = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            out[k] = v
    return out


def main() -> int:
    env = _load_env_file(ROOT / ".env")
    example = _load_env_file(ROOT / ".env.example")
    baseline = {}
    keys = sorted(
        set(env.keys()) | set(example.keys()),
        key=lambda k: (k.startswith("SCALP_"), k),
    )
    for k in keys:
        if not k.startswith(PREFIXES):
            continue
        if any(h in k.upper() for h in SECRET_HINTS):
            continue
        baseline[k] = env.get(k, example.get(k, ""))

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": [".env", ".env.example"],
        "note": "仅含非密钥键；后续行为改动必须与 baseline 对照登记。",
        "values": baseline,
    }
    out_path = ROOT / "config" / "scalp_env_baseline_2026-08-11.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("已导出 %s 个键 -> %s" % (len(baseline), out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
