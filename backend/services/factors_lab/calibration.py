# -*- coding: utf-8 -*-
"""校准模块 —— WorldQuant 101 金样本回归门禁（v0.3 纪律：校准集回归）。

口径：库内 102 条 alpha101 公式（data/discovered_factors.json, category=alpha101）
过 formula_ops 受限 eval 引擎，在固定种子合成 OHLCV 上产出值序列指纹；
首次运行建立金样本（golden），此后每次运行与金样本逐条比对（容差断言），
任何引擎改动导致的数值漂移都会被捕获 —— 这就是"引擎回归门禁"。
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Dict, List

import numpy as np

from backend.services.factors_lab import config

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STORE_CANDIDATES = (
    _REPO_ROOT / "data" / "discovered_factors.json",           # 主存储（custom_factor_store 实际位置）
    Path(__file__).resolve().parents[2] / "data" / "discovered_factors.json",  # 兼容 CWD 变体
)
_STORE = next((p for p in _STORE_CANDIDATES if p.exists()), _STORE_CANDIDATES[0])
_TOL = 1e-10


def _alpha101_formulas() -> List[Dict[str, str]]:
    try:
        d = json.loads(_STORE.read_text(encoding="utf-8"))
        return [{"name": v.get("name"), "formula": v.get("formula")}
                for v in d.values()
                if isinstance(v, dict) and v.get("category") == "alpha101" and v.get("formula")]
    except Exception:
        return []


def _gtja191_formulas() -> List[Dict[str, str]]:
    """[AR-3 补齐 2026-09-17] GTJA 191 校准子集（单序列适配版）。

    [F364 2026-09-18 文档同步] 原 docstring 写"10条"，但数据文件后来扩到 **v2/30 条**
    （见 `backend/data/factors_lab/gtja191_subset.json` 的 `_meta.name`）⇒ **文档漂移**。
    现改为不写死条数：条数以数据文件的 `formulas` 长度为准（读不到则回退空列表，
    校准集会静默少一批样本——因此下面在读失败时**记一条 warning**，避免"少算却无人知"）。
    """
    try:
        p = Path(__file__).resolve().parents[2] / "data" / "factors_lab" / "gtja191_subset.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        out = [{"name": f.get("id"), "formula": f.get("formula")}
               for f in d.get("formulas") or [] if f.get("formula")]
        if not out:
            logger.warning("[Calibration] GTJA191 子集为空或结构异常: %s", p)
        return out
    except Exception as exc:
        logger.warning("[Calibration] GTJA191 子集读取失败（校准集将少这批样本）: %s", str(exc)[:120])
        return []


def _fingerprint(formula: str, fields: Dict[str, np.ndarray]) -> Dict[str, object]:
    from backend.services.factor_engine.formula_ops import FORMULA_OPS
    ns = {"np": np, **fields, **FORMULA_OPS}
    vals = eval(formula, {"__builtins__": {}}, ns)  # noqa: S307 受限命名空间（与 llm_proposal 同构）
    arr = np.asarray(vals, dtype=float)
    finite = arr[np.isfinite(arr)]
    rng = np.random.default_rng(11)
    sample = finite[rng.choice(len(finite), size=min(16, len(finite)), replace=False)] if len(finite) else np.array([])
    return {
        "n": int(arr.size), "finite": int(len(finite)),
        "sum": float(finite.sum()) if len(finite) else 0.0,
        "std": float(finite.std()) if len(finite) else 0.0,
        "sample_md5": hashlib.md5(np.round(sample, 12).tobytes()).hexdigest()[:10],
    }


def run_calibration(*, rebuild_golden: bool = False) -> Dict[str, object]:
    formulas = _alpha101_formulas() + _gtja191_formulas()
    if not formulas:
        return {"ok": False, "error": "alpha101 公式源缺失（discovered_factors.json）"}
    from backend.services.factors_lab.agent3_engineer import synth_ohlcv
    fields = synth_ohlcv(n=400, seed=2026)

    golden: Dict[str, Dict] = {}
    if not rebuild_golden and config.golden_path().exists():
        try:
            golden = json.loads(config.golden_path().read_text(encoding="utf-8"))
        except Exception:
            golden = {}

    passed, failed, errors = 0, [], []
    for f in formulas:
        name = f["name"]
        try:
            fp = _fingerprint(f["formula"], fields)
        except Exception as e:  # noqa: BLE001
            errors.append({"name": name, "error": str(e)[:80]})
            continue
        if name not in golden:
            golden[name] = fp
            passed += 1
            continue
        g = golden[name]
        ok = (abs(fp["sum"] - g["sum"]) <= _TOL * max(1.0, abs(g["sum"]))
              and abs(fp["std"] - g["std"]) <= _TOL * max(1.0, abs(g["std"]))
              and fp["sample_md5"] == g["sample_md5"])
        if ok:
            passed += 1
        else:
            failed.append({"name": name, "golden": g, "current": fp})

    config.golden_path().write_text(json.dumps(golden, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    result = {
        "ok": not failed, "n_formulas": len(formulas),
        "passed": passed, "failed": len(failed), "errors": len(errors),
        "golden_entries": len(golden),
        "failed_detail": failed[:5], "error_detail": errors[:5],
        "tolerance": _TOL,
    }
    logger.info("[FactorsLab校准] %s", json.dumps({k: result[k] for k in
                                                   ("ok", "n_formulas", "passed", "failed", "errors")}))
    return result
