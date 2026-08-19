"""
算力中心配置服务（v6 第十章 前端仪表台配套）。

统一管理「本地算力与开源内嵌」相关环境变量配置项：
- 定义每个配置项（键/默认值/类型/范围/分组/说明），默认值与实际代码逐项核对
- `get_all()`：合并当前生效值（os.environ 优先 → 覆盖文件其次 → 默认值兜底）
- `update(updates)`：校验后写入独立覆盖文件 `backend/config/compute_overrides.env`
  （不碰主 .env，避免写坏唯一配置源），并立即注入 os.environ 使运行中任务生效
- 覆盖文件在模块加载时 lazy 合并进 os.environ（重启后依然生效）
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_OVERRIDES_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "config", "compute_overrides.env")
)

# ────────────────────────────────────────────────────────────
# 配置项定义表（键 → 元信息；默认值与 gp_miner/mcts_miner/scalp_meta_trainer/
# factor_evolution_loop 中的代码默认值逐项核对一致）
# ────────────────────────────────────────────────────────────
# fmt: off
CONFIG_SPECS: Dict[str, Dict[str, Any]] = {
    # ── GP 因子挖掘（factor_evolution_loop → GPConfig）──
    "FACTOR_GP_POPULATION":  {"default": 300, "type": int,   "min": 50,   "max": 2000, "group": "gp",    "label": "GP 种群大小",      "desc": "每代种群个体数（GPConfig.population_size）"},
    "FACTOR_GP_GENERATIONS": {"default": 20,  "type": int,   "min": 1,    "max": 200,  "group": "gp",    "label": "GP 进化代数",      "desc": "GPConfig.generations"},
    "FACTOR_GP_SEEDS":       {"default": 6,   "type": int,   "min": 1,    "max": 32,   "group": "gp",    "label": "GP 并行种子数",    "desc": "幻方 6 种子方法论（GPConfig.n_seeds）"},
    "FACTOR_GP_MAX_WORKERS": {"default": 8,  "type": int,   "min": 1,    "max": 64,   "group": "gp",    "label": "GP 并行评估线程",   "desc": "joblib loky 进程数（[2026-08-16] 32→8：挖矿不再占满全核导致整机 100%）"},
    # ── MCTS 因子挖掘（factor_evolution_loop → MCTSConfig）──
    "FACTOR_MCTS_ENABLED":   {"default": 1,   "type": bool,  "min": None, "max": None, "group": "mcts",  "label": "MCTS 挖掘开关",    "desc": "FACTOR_MCTS_ENABLED=0 关闭 MCTS 挖掘器"},
    "FACTOR_MCTS_ITERATIONS":{"default": 300, "type": int,   "min": 50,   "max": 5000, "group": "mcts",  "label": "MCTS 迭代预算",    "desc": "每棵树的 UCT 迭代数（MCTSConfig.n_iterations）"},
    "FACTOR_MCTS_ROOTS":     {"default": 3,   "type": int,   "min": 1,    "max": 16,   "group": "mcts",  "label": "MCTS 树根数",      "desc": "MCTSConfig.n_roots（短板种子+随机根补齐）"},
    "FACTOR_MCTS_CHILDREN":  {"default": 5,   "type": int,   "min": 2,    "max": 32,   "group": "mcts",  "label": "MCTS 扩展子节点",  "desc": "MCTSConfig.n_children"},
    "FACTOR_MCTS_MAX_WORKERS":{"default": 8,  "type": int,   "min": 1,    "max": 64,   "group": "mcts",  "label": "MCTS 并行线程",     "desc": "MCTSConfig.max_workers（0=min(8,cpu)）"},
    # ── GPU 批量求值（factor_evolution_loop → GpuEvalContext；GTX 1070 8G）──
    "FACTOR_EVO_GPU_EVAL": {"default": 0, "type": bool, "min": None, "max": None, "group": "gpu", "label": "GPU 批量求值开关", "desc": "FACTOR_EVO_GPU_EVAL=1 且 CUDA 可用 → GP/MCTS 栈式 GPU 批量求值（等价性验证失败自动回退 loky CPU）"},
    "FACTOR_EVO_GPU_MAX_MEM_MB": {"default": 1200, "type": int, "min": 128, "max": 7000, "group": "gpu", "label": "GPU 显存预算(MB)", "desc": "批量求值显存预算（WDDM 桌面模式实际可用 ~6.4GB）"},
    "FACTOR_EVO_GPU_CHUNK": {"default": 64, "type": int, "min": 8, "max": 512, "group": "gpu", "label": "GPU 批量块大小", "desc": "每批求值个体数（GpuEvalContext.chunk）"},
    "FACTOR_EVO_GPU_POP_BOOST": {"default": 1, "type": bool, "min": None, "max": None, "group": "gpu", "label": "GPU 种群扩容", "desc": "未显式设 FACTOR_GP_POPULATION 时自动 300→1200"},
    # ── 元标签模型（scalp_meta_trainer）──
    "SCALP_META_MIN_SAMPLES":   {"default": 800,  "type": int,   "min": 200,  "max": 100000, "group": "meta", "label": "元标签最小样本",   "desc": "去重后独立样本门槛（不足优雅跳过）"},
    "SCALP_META_MIN_PER_CLASS": {"default": 200,  "type": int,   "min": 50,   "max": 20000,  "group": "meta", "label": "每类最小样本",     "desc": "赢/亏类各需 ≥ 此数"},
    "SCALP_META_FOLDS":         {"default": 4,    "type": int,   "min": 3,    "max": 10,     "group": "meta", "label": "Walk-Forward 折数", "desc": "时间切分折数"},
    "SCALP_META_GATE_AUC":      {"default": 0.53, "type": float, "min": 0.5,  "max": 0.95,   "group": "meta", "label": "可用门控 AUC",     "desc": "样本外 AUC ≥ 此值才标记 usable"},
    "SCALP_META_HORIZON_SEC":   {"default": 1800, "type": int,   "min": 60,   "max": 86400,  "group": "meta", "label": "结算周期(去重窗)", "desc": "标签窗口/去重窗口（秒）"},
    "SCALP_META_FEATURE_FREQ":  {"default": 0.2,  "type": float, "min": 0.01, "max": 1.0,    "group": "meta", "label": "特征入选频率",     "desc": "快照键出现频率 ≥ 此值才作为特征"},
    # ── 因子进化闭环（factor_evolution_loop）──
    "FACTOR_EVO_TRAIN_DAYS": {"default": 90, "type": int, "min": 10, "max": 3650, "group": "evo", "label": "训练窗口(天)", "desc": "三层切分训练集（4h 档默认 90 天）"},
    "FACTOR_EVO_VAL_DAYS":   {"default": 30, "type": int, "min": 5,  "max": 1825, "group": "evo", "label": "验证窗口(天)", "desc": "样本外 IC 评分集"},
    "FACTOR_EVO_TEST_DAYS":  {"default": 0,  "type": int, "min": 0,  "max": 1825, "group": "evo", "label": "测试窗口(天)", "desc": "0=不启用测试集终审（4h 档默认 30，按周期分档）"},
    "FACTOR_CODEGEN_ENABLED": {"default": 1, "type": bool, "min": None, "max": None, "group": "evo", "label": "LLM Codegen 开关", "desc": "FACTOR_CODEGEN_ENABLED=0 关闭 LLM 补挖"},
    "FACTOR_CODEGEN_N": {"default": 8, "type": int, "min": 1, "max": 64, "group": "evo", "label": "LLM 候选数", "desc": "每轮 Codegen 生成公式数"},
    "FACTOR_MINE_SYMBOLS": {"default": 5, "type": int, "min": 1, "max": 32, "group": "evo", "label": "挖矿币数", "desc": "多币拼接面板最多用几币"},
    "FACTOR_EVO_GATE_FAIL_CLOSED": {"default": 1, "type": bool, "min": None, "max": None, "group": "evo", "label": "进化门禁 fail-closed", "desc": "WFO/测试集/capacity 异常时拒绝而非放行"},
    "FACTOR_MIN_NET_IC": {"default": 0.02, "type": float, "min": 0.0, "max": 1.0, "group": "evo", "label": "最小净 IC", "desc": "晋升门槛净 IC（FACTOR_MIN_NET_IC）"},
    "FACTOR_MINING_BOOST_AUTO": {"default": 0, "type": bool, "min": None, "max": None, "group": "evo", "label": "挖矿加强自动", "desc": "开启后每日定时/手动进化前自动套用 mining_boost 预设"},
    # ── 止盈止损网格训练（tp_sl_grid_trainer）──
    "RISK_TP_SL_TRAIN_AUTO": {
        "default": 1, "type": bool, "min": None, "max": None, "group": "risk",
        "label": "TP/SL 自动训练",
        "desc": "开启后每日定时自动网格搜索止盈止损；结果写入 tp_sl_learned/latest.json",
    },
    "RISK_USE_LEARNED_TP_SL": {
        "default": 1, "type": bool, "min": None, "max": None, "group": "risk",
        "label": "开仓使用学习 TP/SL",
        "desc": "开启后开仓用训练结果覆盖静态表",
    },
    # ── 本地 LLM 双机（gate_optimizer_service）──
    "LOCAL_LLM_CONFIG_ID":   {"default": 0,   "type": int, "min": 0, "max": 9999, "group": "llm", "label": "本地 LLM 配置 ID", "desc": "0=禁用本地 LLM 门控优化；>0 时读取 LLMConfig 表对应配置"},
}
# fmt: on


def _bool_val(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _validate(key: str, value: Any) -> Tuple[Optional[Any], Optional[str]]:
    """校验并规范化单个配置值。返回 (规范值, 错误信息)。"""
    spec = CONFIG_SPECS.get(key)
    if not spec:
        return None, f"未知配置项: {key}"
    vtype = spec["type"]
    try:
        if vtype is bool:
            v = _bool_val(value)
        elif vtype is int:
            v = int(value)
        else:
            v = float(value)
    except (TypeError, ValueError):
        return None, f"{key} 需要 {vtype.__name__} 类型，收到: {value!r}"
    if spec.get("min") is not None and v < spec["min"]:
        return None, f"{key}={v} 低于下限 {spec['min']}"
    if spec.get("max") is not None and v > spec["max"]:
        return None, f"{key}={v} 超过上限 {spec['max']}"
    return v, None


def _load_overrides() -> Dict[str, str]:
    """读取覆盖文件 → dict（不注入 env，由 apply 统一处理）。"""
    out: Dict[str, str] = {}
    try:
        with open(_OVERRIDES_PATH, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                if k in CONFIG_SPECS:
                    out[k] = v.strip()
    except FileNotFoundError:
        pass
    except Exception as e:  # noqa: BLE001
        logger.warning("[ComputeConfig] 覆盖文件读取失败: %s", e)
    return out


_loaded = False


def _ensure_loaded() -> None:
    """lazy 合并覆盖文件进 os.environ（进程内一次）。"""
    global _loaded
    if _loaded:
        return
    _loaded = True
    for k, v in _load_overrides().items():
        if k not in os.environ:
            os.environ[k] = v


def get_all() -> List[Dict[str, Any]]:
    """全量配置项当前生效值（env 优先 → 覆盖文件 → 默认值）。"""
    _ensure_loaded()
    overrides = _load_overrides()
    out: List[Dict[str, Any]] = []
    for key, spec in CONFIG_SPECS.items():
        raw = os.environ.get(key)
        if raw is None:
            raw = overrides.get(key)
        if raw is None:
            raw = spec["default"]
        v, err = _validate(key, raw)
        source = "env"
        if key not in os.environ:
            source = "override" if key in overrides else "default"
        out.append({
            "key": key,
            "value": v,
            "raw": str(raw),
            "default": spec["default"],
            "type": spec["type"].__name__,
            "min": spec.get("min"),
            "max": spec.get("max"),
            "group": spec["group"],
            "label": spec["label"],
            "desc": spec["desc"],
            "source": source,
            "error": err,
        })
    return out


def get_group(group: str) -> List[Dict[str, Any]]:
    return [c for c in get_all() if c["group"] == group]


def get_value(key: str) -> Any:
    """取单个配置项规范值（供后端服务内使用）。"""
    for c in get_all():
        if c["key"] == key:
            return c["value"]
    return CONFIG_SPECS.get(key, {}).get("default")


def update(updates: Dict[str, Any]) -> Dict[str, Any]:
    """校验并应用配置更新。

    - 校验失败：返回 {"ok": False, "errors": {...}}，全部不落盘
    - 校验通过：写覆盖文件（保留未改项与注释）+ 注入 os.environ 即时生效
    """
    if not isinstance(updates, dict) or not updates:
        return {"ok": False, "errors": {"__global__": "请求体须为非空对象"}}

    validated: Dict[str, Any] = {}
    errors: Dict[str, str] = {}
    for key, raw in updates.items():
        v, err = _validate(key, raw)
        if err:
            errors[key] = err
        else:
            validated[key] = v
    if errors:
        return {"ok": False, "errors": errors}

    _ensure_loaded()
    overrides = _load_overrides()
    for key, v in validated.items():
        if isinstance(v, bool):
            overrides[key] = "1" if v else "0"
        else:
            overrides[key] = str(v)

    try:
        lines = [
            "# 算力中心配置覆盖（由 /api/compute/config PUT 生成，重启后自动加载）",
            f"# 生成时间: {__import__('datetime').datetime.now().isoformat(timespec='seconds')}",
        ]
        for key in sorted(CONFIG_SPECS):
            if key in overrides:
                lines.append(f"{key}={overrides[key]}")
        lines.append("")
        os.makedirs(os.path.dirname(_OVERRIDES_PATH), exist_ok=True)
        # 原子写：tmp + rename
        tmp_path = _OVERRIDES_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        os.replace(tmp_path, _OVERRIDES_PATH)
    except Exception as e:  # noqa: BLE001
        logger.error("[ComputeConfig] 覆盖文件写入失败: %s", e)
        return {"ok": False, "errors": {"__global__": f"覆盖文件写入失败: {e}"}}

    # 注入运行时环境（即时生效）
    for key, v in validated.items():
        os.environ[key] = "1" if isinstance(v, bool) else str(v)

    applied = [
        {"key": k, "value": validated[k], "source": "override"}
        for k in validated
    ]
    logger.info("[ComputeConfig] 配置已下发: %s", list(validated.keys()))
    return {"ok": True, "applied": applied}


# 命名预设：加强挖矿（不降低 DSR/PBO/相关/漂移门禁）。
# [2026-08-16 深度卡死修复] 不再带 FACTOR_EVO_TRAIN/VAL/TEST_DAYS：
# 旧值 120/40/30 是三层切分按周期分档（_PERIOD_SPLIT_DAYS）之前的残留，
# env 覆盖会压过所有周期档位 → 15m 需 190 天≈18,290 根、5m 需 54,770 根，
# 短周期档永远 depth_insufficient（数据中心深度目标 5m=55d/15m=90d）。
# 加强只加大搜索力度，窗口继续按周期分档（4h 档本身 270 天更深）。
PRESETS: Dict[str, Dict[str, Any]] = {
    "mining_boost": {
        # [2026-08-16 提速校准] 原加强档 population=500/generations=30/codegen=16
        # 让单轮跑 2.5h+、LLM 流把价格接口拖到 7~9s。校准后：
        # - GP 回到默认档 300×20×6（实测贡献有限，时间却线性增长）；
        # - 加强差异保留在 MCTS（500 迭代/5 根 > 默认 300/3）；
        # - codegen 降到 4 条流（默认 8，原加强 16）——补挖质量略降，
        #   但不再拖垮 API；硬时间预算见 factor_evolution_loop
        #   FACTOR_EVO_BUDGET_MAX_SEC（默认 1800s）。
        "FACTOR_GP_POPULATION": 300,
        "FACTOR_GP_GENERATIONS": 20,
        "FACTOR_GP_SEEDS": 6,
        "FACTOR_MCTS_ITERATIONS": 500,
        "FACTOR_MCTS_ROOTS": 5,
        "FACTOR_CODEGEN_ENABLED": 1,
        "FACTOR_CODEGEN_N": 4,
        "FACTOR_MINE_SYMBOLS": 5,
        "FACTOR_EVO_GATE_FAIL_CLOSED": 1,
        "FACTOR_MIN_NET_IC": 0.02,
    },
}


def list_presets() -> List[Dict[str, Any]]:
    return [
        {"name": name, "keys": sorted(spec.keys()), "values": dict(spec)}
        for name, spec in PRESETS.items()
    ]


def apply_preset(name: str) -> Dict[str, Any]:
    spec = PRESETS.get(name)
    if not spec:
        return {"ok": False, "errors": {"__global__": f"未知预设: {name}"}}
    return update(dict(spec))
