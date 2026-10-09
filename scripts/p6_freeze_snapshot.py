# -*- coding: utf-8 -*-
"""[P6 大轮回 2026-09-27] 摸底窗口开窗声明 + 参数冻结快照。

设计 §1.3：连续 30 天窗口内**不改任何参数**（只允许修 bug，且 bug 修复需记录并单独标注）。
本脚本：
  1. 对关键 env 键（风险/执行/出场/学习/配额——即本路线图 P0~P5 落地的全部可调参数）
     计算 SHA256 快照，写入 `data/p6_window_declaration.json`；
  2. 声明文件含：开窗时间、参数清单+值、验收口径（§1.3）、回滚与 bug 修复纪律；
  3. 每日审计（p0_data_contract_audit）比对 SHA，漂移即告警（修复须标注，不自动回滚）。

用法：
  .venv\\Scripts\\python.exe scripts/p6_freeze_snapshot.py            # 开窗/复算并显示
  .venv\\Scripts\\python.exe scripts/p6_freeze_snapshot.py --write    # 落盘声明文件
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 冻结键清单：P0~P5 落地的全部可调参数（前缀白名单）
_KEY_PREFIXES = (
    "MIDLONG_", "P1_", "P4_", "P5_", "TIER_", "EXIT_POLICY_", "PC_",
    "PAPER_", "MLTO_LEARNING_", "REENTRY_", "SCALP_SHORT_", "EXIT_ARBITER_",
    "MACRO_EVENT_", "LIVE_MIN_", "FUNDING_",
)

DECL_PATH = ROOT / "data" / "p6_window_declaration.json"


def frozen_params(env_file: Path = ROOT / ".env") -> Dict[str, str]:
    """从 .env 提取冻结键清单（排序后稳定）。"""
    params: Dict[str, str] = {}
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            if any(k.startswith(p) for p in _KEY_PREFIXES):
                params[k] = v.strip()
    return dict(sorted(params.items()))


def params_sha(params: Dict[str, str]) -> str:
    payload = "\n".join(f"{k}={v}" for k, v in params.items())
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def declaration(params: Dict[str, str], sha: str) -> Dict[str, object]:
    return {
        "window_started_at": datetime.now(timezone.utc).isoformat(),
        "window_days": 30,
        "params_sha": sha,
        "param_count": len(params),
        "params": params,
        "acceptance": {
            "window": "连续 30 个自然日，窗口内不改任何参数（bug 修复需记录并单独标注）",
            "main_metric": "净收益 = Σ(平仓毛盈亏) − 全部手续费 − 资金费",
            "lane_metrics": "每条车道单独报：笔数/胜率/PF/期望/最大回撤/中位持仓时长",
            "sample_gate": "每条车道 ≥200 笔 且 每个 (方向×regime) 桶 ≥40 笔",
            "pass_line": "合计净收益>0 且 两车道均非负 且 回撤≤权益15%",
            "anti_cheat": "去掉最好 5 笔后仍为正",
        },
        "discipline": {
            "no_param_change": True,
            "bug_fix_allowed": "只允许修 bug；修复必须记录并单独标注（台账/本文档）",
            "rollback": "任一 P0~P5 阶段开关仍可回滚（回滚=bug 修复类，需标注）",
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    params = frozen_params()
    sha = params_sha(params)
    dec = declaration(params, sha)
    print(f"[P6] 冻结参数 {len(params)} 项，SHA={sha}")
    if a.write:
        DECL_PATH.parent.mkdir(parents=True, exist_ok=True)
        DECL_PATH.write_text(json.dumps(dec, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[P6] 摸底窗口声明已落盘: {DECL_PATH}")
    else:
        print(f"[P6] （未加 --write，未落盘）")
    return 0


def load_declaration() -> Dict[str, object]:
    if DECL_PATH.is_file():
        return json.loads(DECL_PATH.read_text(encoding="utf-8"))
    return {}


if __name__ == "__main__":
    raise SystemExit(main())
