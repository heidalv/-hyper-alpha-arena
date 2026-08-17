"""CustomFactorStore — 发现因子的持久化目录（阶段二 2.1）。

背景与缺口
==========
`factor_discovery.py`（OpenCode/公式派）在验证通过后调用
`factor_engine.register_custom_factor(...)`，但 `FactorEngine` 从未实现该方法
（调用点用 `hasattr` 保护，于是**静默失败**）——OpenCode 发现的公式因子根本
没有入库。（LLM 派 `ai_factor_discovery` 走的是写 Python 文件 + hot_reload，另一条路。）

本模块补上这条缺口：把发现的**公式因子**（numpy 表达式，变量为 close/high/low/
volume/open）持久化到 `data/discovered_factors.json`，形成可查询、可回测、可晋升的
候选目录。

准入分层（配合 2.2 单因子回测打分闸门）
=====================================
- `register(...)` 只把因子登记为 **candidate（候选）**，不会立刻进入实时合成信号——
  避免未经样本外验证的公式直接影响实盘。
- 回测打分器（`factor_backtest_scorer`）评估后写回 `grade`/`status`：A/B 级 →
  `active`，其余 → `rejected`。
- 只有 `active` 因子才会被短线活跃因子集（2.3）纳入实时使用。

安全
====
公式用受限 `eval`（`__builtins__` 置空，命名空间只暴露 numpy 与 OHLCV 数组），
与 `factor_discovery._validate_factor` 现有做法一致；登记前会在一段假数据上试算，
不能编译/求值的公式直接拒绝。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_STORE_FILE = os.path.join("data", "discovered_factors.json")
_ALLOWED_VARS = {"close", "high", "low", "volume", "open"}


def _slug(name: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z]+", "_", (name or "").strip().lower()).strip("_")
    return s or "factor"


def make_formula_compute(formula: str) -> Callable:
    """把 numpy 公式字符串包装成 compute(klines, market_data)->float 闭包。

    与 FactorEngine.FACTORS 的 compute 签名兼容；求值失败返回 None（不抛异常）。
    """
    import numpy as np

    try:
        code = compile(formula, "<custom_factor>", "eval")
    except Exception as e:
        raise ValueError(f"公式无法编译: {e}")

    def _compute(klines, market_data=None):  # noqa: ANN001
        try:
            import pandas as pd
            df = klines if isinstance(klines, pd.DataFrame) else pd.DataFrame(klines)
            if df is None or len(df) == 0:
                return None
            closes = df["close"].astype(float).to_numpy()
            highs = df["high"].astype(float).to_numpy() if "high" in df else closes
            lows = df["low"].astype(float).to_numpy() if "low" in df else closes
            vols = df["volume"].astype(float).to_numpy() if "volume" in df else np.zeros_like(closes)
            opens = df["open"].astype(float).to_numpy() if "open" in df else np.roll(closes, 1)
            ns = {
                "np": np, "close": closes, "high": highs,
                "low": lows, "volume": vols, "open": opens,
            }
            try:
                from backend.services.factor_engine.formula_ops import FORMULA_OPS
                ns.update(FORMULA_OPS)
            except Exception:
                pass
            vals = eval(code, {"__builtins__": {}}, ns)  # noqa: S307 受限命名空间
            if isinstance(vals, np.ndarray):
                if len(vals) == 0:
                    return None
                v = vals[-1]
            else:
                v = vals
            fv = float(v)
            if fv != fv or fv in (float("inf"), float("-inf")):
                return None
            return fv
        except Exception:
            return None

    return _compute


def _validate_formula(formula: str) -> Optional[str]:
    """在假数据上试算公式；返回错误信息，None 表示通过。"""
    if not formula or not isinstance(formula, str):
        return "公式为空"
    # 变量白名单：出现未知裸标识符时也放行（可能是 np.xxx），只拦明显危险关键字。
    for banned in ("import", "__", "open(", "eval(", "exec(", "os.", "sys.", "subprocess"):
        if banned in formula:
            return f"公式含禁用片段: {banned}"
    try:
        import numpy as np
        compute = make_formula_compute(formula)
        import pandas as pd
        n = 60
        df = pd.DataFrame({
            "open": np.linspace(100, 110, n),
            "high": np.linspace(101, 111, n),
            "low": np.linspace(99, 109, n),
            "close": np.linspace(100, 110, n) + np.random.RandomState(0).randn(n),
            "volume": np.abs(np.random.RandomState(1).randn(n)) * 1000 + 100,
        })
        val = compute(df)
        if val is None:
            return "公式在样本数据上求值失败/返回空"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"公式试算异常: {e}"
    return None


class CustomFactorStore:
    """发现因子的持久化目录（单例，线程安全）。"""

    _instance: Optional["CustomFactorStore"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_state()
        return cls._instance

    def _init_state(self) -> None:
        self._lock = threading.RLock()
        self._data: Dict[str, Dict[str, Any]] = {}
        self._loaded = False
        self._loaded_mtime = 0.0

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._load_locked()
            self._loaded = True

    def _load_locked(self) -> None:
        # [2026-08-16 修复] 多进程瞬时撕裂读（另一进程正在 replace 文件）会导致
        # JSON 解析失败。旧实现直接 self._data = {} —— 空目录随后被 persist 覆写
        # 回磁盘，抹掉全部因子（曾真实发生）。现在：重试退避读取；全部失败时
        # 保留内存旧数据，绝不清空。
        if not os.path.exists(_STORE_FILE):
            return
        for attempt in range(4):
            try:
                with open(_STORE_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    self._data = raw
                elif isinstance(raw, list):
                    self._data = {r["factor_id"]: r for r in raw if r.get("factor_id")}
                self._loaded_mtime = os.path.getmtime(_STORE_FILE)
                return
            except Exception as e:
                logger.warning(
                    f"[CustomFactorStore] 载入失败(第{attempt + 1}次, 退避重试): {e}"
                )
                time.sleep(0.2 * (attempt + 1))
        if not self._data:
            logger.error("[CustomFactorStore] 持久目录多次读取失败且无内存数据，保持空目录")

    def _maybe_reload(self) -> None:
        """[2026-08-15 多进程竞态修复] 写前检测文件是否被其它进程更新。

        多进程（后端 / 独立验证脚本 / 其他 agent 长驻进程）共用同一 JSON 文件，
        各自持有内存副本，last-writer-wins 会互相覆盖（曾导致：18:31 验证晋升的
        A 级因子被 17:08 启动的陈旧副本覆盖回 candidate）。写前按 mtime 检测
        外部更新并重载合并，把「整文件覆盖」降级为「读-改-写」，大幅缩小丢更新窗口。
        """
        try:
            if not os.path.exists(_STORE_FILE):
                return
            st_mtime = os.path.getmtime(_STORE_FILE)
            if st_mtime > self._loaded_mtime + 0.3:
                self._load_locked()
        except Exception:
            pass

    def _persist(self) -> None:
        # [2026-08-16 修复三件套]
        # 1) 空目录防护：内存为空但磁盘已有非空数据 → 拒绝覆写（撕裂读降级保护）。
        # 2) 每进程独立 tmp 名：多进程共用 ".tmp" 会互相截断对方写入。
        # 3) WinError 5/32（另一进程短暂持有文件）重试退避，不再丢写。
        with self._lock:
            if not self._data:
                try:
                    if os.path.exists(_STORE_FILE) and os.path.getsize(_STORE_FILE) > 2:
                        with open(_STORE_FILE, "r", encoding="utf-8") as f:
                            _disk = json.loads(f.read())
                        if _disk:
                            logger.error(
                                "[CustomFactorStore] 拒绝以空目录覆写非空文件（撕裂读保护）"
                            )
                            return
                except Exception:
                    pass
            tmp = f"{_STORE_FILE}.{os.getpid()}.tmp"
            _last_err: Exception | None = None
            for _attempt in range(5):
                try:
                    os.makedirs(os.path.dirname(_STORE_FILE), exist_ok=True)
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(self._data, f, ensure_ascii=False, indent=2)
                    os.replace(tmp, _STORE_FILE)
                    self._loaded_mtime = os.path.getmtime(_STORE_FILE)
                    return
                except Exception as e:
                    _last_err = e
                    time.sleep(0.25 * (_attempt + 1))
            logger.warning(f"[CustomFactorStore] 持久化失败(重试5次): {_last_err}")

    def register(
        self,
        name: str,
        formula: str,
        category: str = "discovered",
        ic: Optional[float] = None,
        rank_ic: Optional[float] = None,
        source: str = "opencode",
        extra: Optional[Dict[str, Any]] = None,
        tenant_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """登记一个候选公式因子（幂等）。必须带 tenant_id，避免跨账户混淆。

        Returns:
            {"ok": bool, "factor_id": str, "status": str, "reason": str}
        """
        self._ensure_loaded()
        self._maybe_reload()
        if tenant_id is None:
            try:
                from backend.core.tenant import tenant_id_var
                tenant_id = tenant_id_var.get()
            except Exception:
                tenant_id = None
        if tenant_id is None:
            return {
                "ok": False,
                "factor_id": "",
                "status": "invalid",
                "reason": "tenant_id required（发现因子按账户隔离）",
            }

        err = _validate_formula(formula)
        if err:
            logger.info(f"[CustomFactorStore] 拒绝登记 {name}: {err}")
            return {"ok": False, "factor_id": "", "status": "invalid", "reason": err}

        factor_id = f"ai_{_slug(name)}"
        key = f"t{int(tenant_id)}:{factor_id}"
        now = time.time()
        with self._lock:
            existing = self._data.get(key)
            if existing:
                # [2026-08-14 P2-1 修复] 公式实际变化时重置评分状态：
                # 旧实现保留 rejected 的 status/grade/scores → 被拒因子换公式后
                # 永远无法重新进入 candidate 复评（"一键挖矿/定时验证"二次起空转）。
                # 公式未变时保持原状态（幂等种子语义，避免相同公式反复入队）。
                _formula_changed = str(existing.get("formula") or "") != str(formula or "")
                existing["formula"] = formula
                existing["discovery_ic"] = ic
                existing["discovery_rank_ic"] = rank_ic
                existing["updated_at"] = now
                existing["tenant_id"] = int(tenant_id)
                if not existing.get("category"):
                    existing["category"] = category or "discovered"
                if not existing.get("name"):
                    existing["name"] = name
                if not existing.get("source"):
                    existing["source"] = source
                if extra:
                    existing.setdefault("extra", {}).update(extra)
                if _formula_changed:
                    existing["status"] = "candidate"
                    existing["grade"] = None
                    existing["scores"] = {}
                    existing.pop("scored_at", None)
                self._persist()
                return {
                    "ok": True, "factor_id": factor_id,
                    "status": existing.get("status", "candidate"),
                    "reason": "reopened" if _formula_changed else "updated",
                }
            record = {
                "factor_id": factor_id,
                "tenant_id": int(tenant_id),
                "name": name,
                "category": category or "discovered",
                "formula": formula,
                "source": source,
                "discovery_ic": ic,
                "discovery_rank_ic": rank_ic,
                "status": "candidate",   # candidate / active / rejected
                "grade": None,           # A / B / C / D（由回测打分器写回）
                "scores": {},
                "created_at": now,
                "updated_at": now,
                "extra": extra or {},
            }
            self._data[key] = record
            self._persist()
        logger.info(
            f"[CustomFactorStore] 已登记候选因子 {factor_id} tenant={tenant_id} (source={source})"
        )
        return {"ok": True, "factor_id": factor_id, "status": "candidate", "reason": "registered"}

    def _resolve_key(self, factor_id: str, tenant_id: Optional[int] = None) -> Optional[str]:
        """按租户解析存储 key；兼容历史无前缀条目（仅同名精确匹配且无 tenant）。"""
        if tenant_id is not None:
            key = f"t{int(tenant_id)}:{factor_id}"
            if key in self._data:
                return key
        # 精确旧 key（无租户）——仅当调用方显式不传 tenant 时用于迁移/运维
        if tenant_id is None and factor_id in self._data:
            return factor_id
        return None

    def update_scores(
        self,
        factor_id: str,
        grade: str,
        scores: Dict[str, Any],
        status: Optional[str] = None,
        tenant_id: Optional[int] = None,
    ) -> bool:
        """回测打分器写回评级/明细/状态。"""
        self._ensure_loaded()
        self._maybe_reload()
        with self._lock:
            key = self._resolve_key(factor_id, tenant_id)
            if not key:
                return False
            rec = self._data.get(key)
            if not rec:
                return False
            if tenant_id is not None and rec.get("tenant_id") not in (None, int(tenant_id)):
                return False
            rec["grade"] = grade
            rec["scores"] = scores or {}
            rec["scored_at"] = time.time()
            if status:
                rec["status"] = status
            rec["updated_at"] = time.time()
            self._persist()
        return True

    def set_status(self, factor_id: str, status: str, tenant_id: Optional[int] = None) -> bool:
        self._ensure_loaded()
        self._maybe_reload()
        with self._lock:
            key = self._resolve_key(factor_id, tenant_id)
            if not key:
                return False
            rec = self._data.get(key)
            if not rec:
                return False
            if tenant_id is not None and rec.get("tenant_id") not in (None, int(tenant_id)):
                return False
            rec["status"] = status
            rec["updated_at"] = time.time()
            self._persist()
        return True

    def reopen_rejected(
        self,
        tenant_id: Optional[int] = None,
        category: Optional[str] = None,
    ) -> int:
        """[2026-08-14 P2-1] 把 rejected 因子重新开为 candidate（供重评）。

        用途：闸门阈值/引擎修复后，批量重新验证此前被拒因子；
        或"一键挖矿"重灌后让全部因子重新进入验证队列。
        Returns: 重开的因子数。
        """
        self._ensure_loaded()
        self._maybe_reload()
        reopened = 0
        with self._lock:
            for rec in self._data.values():
                if tenant_id is not None and rec.get("tenant_id") not in (None, int(tenant_id)):
                    continue
                if category and str(rec.get("category") or "") != category:
                    continue
                if rec.get("status") != "rejected":
                    continue
                rec["status"] = "candidate"
                rec["grade"] = None
                rec["scores"] = {}
                rec.pop("scored_at", None)
                rec["updated_at"] = time.time()
                reopened += 1
            if reopened:
                self._persist()
        logger.info("[CustomFactorStore] reopen_rejected: 重开 %d 个因子", reopened)
        return reopened

    def register_reference(
        self,
        factor_id: str,
        *,
        tenant_id: Optional[int] = None,
        horizon: str = "midlong",
        timeframe: str = "4h",
        category: str = "registry",
        source: str = "registry",
        registry_factor_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """[2026-08-14 弹药扩源] 登记 registry Python 类因子的引用记录。

        与 register 的区别：无公式（kind=registry）。因子值由
        FactorService/FactorCalculator 按 registry_factor_id 计算；评分由
        midlong_registry_factors.scan_registry_midlong 用同一闸门引擎回写。
        """
        self._ensure_loaded()
        self._maybe_reload()
        if tenant_id is None:
            try:
                from backend.core.tenant import tenant_id_var
                tenant_id = tenant_id_var.get()
            except Exception:
                tenant_id = None
        if tenant_id is None:
            return {"ok": False, "factor_id": factor_id, "status": "invalid",
                    "reason": "tenant_id required"}
        fid = str(factor_id or "").strip()
        if not fid:
            return {"ok": False, "factor_id": "", "status": "invalid", "reason": "factor_id required"}
        _reg_fid = str(registry_factor_id or fid)
        key = f"t{int(tenant_id)}:{fid}"
        now = time.time()
        with self._lock:
            existing = self._data.get(key)
            if existing is not None and str(existing.get("formula") or ""):
                # 同名公式记录已存在：不覆盖公式轨道，只补标记并返回现有
                existing.setdefault("extra", {}).setdefault("kind", "registry")
                existing.setdefault("extra", {}).setdefault("horizon", horizon)
                existing.setdefault("extra", {}).setdefault("timeframe", timeframe)
                self._persist()
                return {"ok": True, "factor_id": fid, "status": existing.get("status", "candidate"),
                        "reason": "merged"}
            if existing is None:
                self._data[key] = {
                    "factor_id": fid,
                    "tenant_id": int(tenant_id),
                    "name": _reg_fid,
                    "category": category,
                    "formula": None,          # registry 因子无公式（Python 类计算）
                    "source": source,
                    "discovery_ic": None,
                    "discovery_rank_ic": None,
                    "status": "candidate",
                    "grade": None,
                    "scores": {},
                    "created_at": now,
                    "updated_at": now,
                    "extra": {
                        "horizon": horizon, "timeframe": timeframe, "kind": "registry",
                        "registry_factor_id": _reg_fid,
                    },
                }
                self._persist()
                return {"ok": True, "factor_id": fid, "status": "candidate", "reason": "registered"}
            # [2026-08-15] 引用记录已存在：rejected → 重开 candidate（评分路径修复后
            # 需重扫，否则每日扫描永远找不到候选、二次挖矿空转）。
            if str(existing.get("status")) == "rejected":
                existing["status"] = "candidate"
                existing["grade"] = None
                existing["scores"] = {}
                existing["updated_at"] = now
                self._persist()
                return {"ok": True, "factor_id": fid, "status": "candidate", "reason": "reopened"}
            return {"ok": True, "factor_id": fid, "status": existing.get("status", "candidate"),
                    "reason": "exists"}
        return {"ok": True, "factor_id": fid, "status": "candidate", "reason": "registered"}

    def get(self, factor_id: str, tenant_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        self._ensure_loaded()
        self._maybe_reload()
        with self._lock:
            key = self._resolve_key(factor_id, tenant_id)
            if not key:
                return None
            rec = self._data.get(key)
            if not rec:
                return None
            if tenant_id is not None and rec.get("tenant_id") not in (None, int(tenant_id)):
                return None
            return dict(rec)

    def list(
        self,
        status: Optional[str] = None,
        tenant_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """列出因子。传入 tenant_id 时只返回该账户挖掘因子；不含历史无主记录。"""
        self._ensure_loaded()
        self._maybe_reload()
        with self._lock:
            items = list(self._data.values())
        if tenant_id is not None:
            tid = int(tenant_id)
            items = [r for r in items if r.get("tenant_id") == tid]
        else:
            # 未指定租户时不返回任何发现因子（防止误当成全平台共享）
            items = []
        if status:
            items = [r for r in items if r.get("status") == status]
        return [dict(r) for r in items]

    def list_candidates(self, tenant_id: Optional[int] = None) -> List[Dict[str, Any]]:
        return self.list(status="candidate", tenant_id=tenant_id)

    def list_active(self, tenant_id: Optional[int] = None) -> List[Dict[str, Any]]:
        return self.list(status="active", tenant_id=tenant_id)


# 全局单例
custom_factor_store = CustomFactorStore()