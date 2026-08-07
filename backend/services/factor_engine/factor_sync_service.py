"""
FactorSyncService — 云端因子库同步服务

核心流程：
1. download_factors()  — 从云端仓库下载因子定义 JSON
2. validate_factor()   — 安全验证因子代码
3. localize_factor()   — JSON → Python BaseFactor 类文件
4. register_factor()   — 注册到 FactorRegistry
"""

import json
import logging
import os
import re
import subprocess
import tempfile
import shutil
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 安全黑名单（与 ai_factor_discovery_service.py 保持一致）
_FORBIDDEN_PATTERNS = [
    "os.system", "subprocess", "eval(", "exec(", "__import__",
    "open(", "shutil", "socket", "requests", "os.remove",
    "os.rmdir", "sys.exit", "ctypes",
]


class FactorSyncService:
    """云端因子库同步与本地化服务"""

    def __init__(self):
        self._project_root = self._detect_project_root()
        self._external_dir = os.path.join(
            self._project_root,
            "backend", "services", "factor_engine", "factors", "external",
        )

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def sync_from_repo(self, config_id: int = None) -> Dict:
        """
        完整同步流程：下载 → 验证 → 本地化 → 注册。

        Args:
            config_id: 指定同步配置 ID（None=全部启用的配置）

        Returns:
            同步结果摘要
        """
        from backend.database.connection import SessionLocal
        from backend.database.models import FactorSyncConfig, CloudFactorDefinition

        with SessionLocal() as db:
            if config_id:
                configs = [db.query(FactorSyncConfig).get(config_id)]
            else:
                configs = db.query(FactorSyncConfig).filter(
                    FactorSyncConfig.enabled == True  # noqa: E712
                ).all()

            if not configs:
                return {"status": "skipped", "reason": "no enabled sync configs"}

            total_downloaded = 0
            total_localized = 0
            total_errors = 0

            for cfg in configs:
                cfg.last_sync_status = "running"
                cfg.last_sync_at = datetime.now(timezone.utc)
                db.commit()

                try:
                    # 1. 下载因子定义
                    definitions = self._download_from_repo(cfg)
                    total_downloaded += len(definitions)

                    # 2. 逐个处理
                    for defn in definitions:
                        try:
                            # 存入数据库
                            self._upsert_cloud_factor(db, defn, cfg.repo_url)

                            # 安全验证
                            if not self._validate_code(defn.get("calculation_code", "")):
                                self._update_factor_status(
                                    db, defn["factor_id"], "error",
                                    "安全验证失败：包含禁止的代码模式"
                                )
                                total_errors += 1
                                continue

                            # 本地化
                            local_path = self._localize_factor(defn)
                            if local_path:
                                self._update_factor_status(
                                    db, defn["factor_id"], "localized",
                                    localized_path=local_path,
                                )
                                total_localized += 1

                                # 注册到 FactorRegistry
                                self._register_localized_factor(local_path, defn)
                                self._update_factor_status(
                                    db, defn["factor_id"], "active"
                                )
                            else:
                                total_errors += 1

                        except Exception as e:
                            logger.error(f"[FactorSync] 因子 {defn.get('factor_id')} 处理失败: {e}")
                            self._update_factor_status(
                                db, defn.get("factor_id", "unknown"), "error", str(e)[:200]
                            )
                            total_errors += 1

                    cfg.last_sync_status = "success"
                    cfg.factors_downloaded = total_downloaded
                    cfg.factors_registered = total_localized
                    cfg.last_sync_log = f"downloaded={total_downloaded} localized={total_localized} errors={total_errors}"

                except Exception as e:
                    cfg.last_sync_status = "failed"
                    cfg.last_sync_log = str(e)[:500]
                    logger.error(f"[FactorSync] 仓库 {cfg.name} 同步失败: {e}")

                db.commit()

        return {
            "status": "completed",
            "downloaded": total_downloaded,
            "localized": total_localized,
            "errors": total_errors,
        }

    def get_sync_status(self) -> List[Dict]:
        """获取所有同步配置的状态"""
        from backend.database.connection import SessionLocal
        from backend.database.models import FactorSyncConfig

        with SessionLocal() as db:
            configs = db.query(FactorSyncConfig).all()
            return [
                {
                    "id": c.id,
                    "name": c.name,
                    "repo_url": c.repo_url,
                    "branch": c.branch,
                    "enabled": c.enabled,
                    "auto_sync": c.auto_sync,
                    "last_sync_at": str(c.last_sync_at) if c.last_sync_at else None,
                    "last_sync_status": c.last_sync_status,
                    "factors_downloaded": c.factors_downloaded,
                    "factors_registered": c.factors_registered,
                }
                for c in configs
            ]

    def list_cloud_factors(self, status: Optional[str] = None) -> List[Dict]:
        """列出云端因子定义"""
        from backend.database.connection import SessionLocal
        from backend.database.models import CloudFactorDefinition

        with SessionLocal() as db:
            query = db.query(CloudFactorDefinition)
            if status:
                query = query.filter(CloudFactorDefinition.status == status)
            factors = query.order_by(CloudFactorDefinition.downloaded_at.desc()).all()
            return [
                {
                    "id": f.id,
                    "factor_id": f.factor_id,
                    "name": f.name,
                    "display_name": f.display_name,
                    "category": f.category,
                    "subcategory": f.subcategory,
                    "status": f.status,
                    "localized": f.localized,
                    "source_repo": f.source_repo,
                    "downloaded_at": str(f.downloaded_at) if f.downloaded_at else None,
                    "localized_at": str(f.localized_at) if f.localized_at else None,
                    "error_message": f.error_message,
                }
                for f in factors
            ]

    def localize_single_factor(self, factor_id: str) -> Dict:
        """手动触发单个因子的本地化"""
        from backend.database.connection import SessionLocal
        from backend.database.models import CloudFactorDefinition

        with SessionLocal() as db:
            factor = db.query(CloudFactorDefinition).filter(
                CloudFactorDefinition.factor_id == factor_id
            ).first()

            if not factor:
                return {"status": "error", "reason": f"factor {factor_id} not found"}

            if not self._validate_code(factor.calculation_code or ""):
                return {"status": "error", "reason": "security validation failed"}

            defn = {
                "factor_id": factor.factor_id,
                "name": factor.name,
                "display_name": factor.display_name or factor.name,
                "description": factor.description or "",
                "category": factor.category,
                "subcategory": factor.subcategory or "",
                "calculation_code": factor.calculation_code or "",
                "parameters": factor.parameters or {},
                "version": factor.version or "1.0.0",
                "author": factor.author or "Cloud Sync",
            }

            local_path = self._localize_factor(defn)
            if local_path:
                factor.localized = True
                factor.localized_path = local_path
                factor.localized_at = datetime.now(timezone.utc)
                factor.status = "localized"
                db.commit()

                self._register_localized_factor(local_path, defn)
                factor.status = "active"
                db.commit()

                return {"status": "success", "factor_id": factor_id, "path": local_path}

            return {"status": "error", "reason": "localization failed"}

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _detect_project_root(self) -> str:
        """检测项目根目录"""
        current = os.path.dirname(os.path.abspath(__file__))
        # 文件位于 backend/services/factor_engine/，向上3级到项目根
        return os.path.dirname(os.path.dirname(os.path.dirname(current)))

    def _download_from_repo(self, config) -> List[Dict]:
        """
        从云端仓库下载因子定义。

        支持:
        1. Git 仓库 (HTTPS) → clone/pull + 扫描 JSON 文件
        2. 本地目录 → 直接扫描 JSON 文件
        """
        definitions = []
        repo_url = config.repo_url

        # 本地目录模式
        if os.path.isdir(repo_url):
            definitions = self._scan_factor_directory(repo_url)
            return definitions

        # Git 仓库模式
        tmp_dir = tempfile.mkdtemp(prefix="factor_sync_")
        try:
            clone_cmd = [
                "git", "clone", "--depth", "1",
                "--branch", config.branch or "main",
                repo_url, tmp_dir,
            ]
            result = subprocess.run(
                clone_cmd, capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                raise RuntimeError(f"git clone failed: {result.stderr[:200]}")

            scan_dir = tmp_dir
            if config.sync_path:
                scan_dir = os.path.join(tmp_dir, config.sync_path)
                if not os.path.isdir(scan_dir):
                    raise RuntimeError(f"sync_path not found: {config.sync_path}")

            definitions = self._scan_factor_directory(scan_dir)

        except FileNotFoundError:
            raise RuntimeError("git not found, install git or use local directory")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        return definitions

    def _scan_factor_directory(self, directory: str) -> List[Dict]:
        """扫描目录中的因子定义 JSON 文件"""
        definitions = []
        if not os.path.isdir(directory):
            return definitions

        for root, dirs, files in os.walk(directory):
            # 跳过 __pycache__ 和 .git
            dirs[:] = [d for d in dirs if not d.startswith((".", "__"))]
            for fname in files:
                if fname.endswith(".json"):
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        # 支持单文件多因子（数组）和单因子（对象）
                        if isinstance(data, list):
                            for item in data:
                                if self._is_valid_definition(item):
                                    definitions.append(item)
                        elif isinstance(data, dict):
                            if self._is_valid_definition(data):
                                definitions.append(data)
                    except Exception as e:
                        logger.debug(f"[FactorSync] 跳过 {fpath}: {e}")

        logger.info(f"[FactorSync] 扫描 {directory}，找到 {len(definitions)} 个因子定义")
        return definitions

    def _is_valid_definition(self, data: Dict) -> bool:
        """验证因子定义是否包含必要字段"""
        required = ("factor_id", "name", "category", "calculation_code")
        return all(k in data for k in required)

    def _validate_code(self, code: str) -> bool:
        """安全验证因子代码"""
        code_lower = code.lower()
        for pattern in _FORBIDDEN_PATTERNS:
            if pattern in code_lower:
                logger.warning(f"[FactorSync] 代码包含禁止模式: {pattern}")
                return False
        try:
            compile(code, "<factor_validation>", "exec")
        except SyntaxError as e:
            logger.warning(f"[FactorSync] 语法错误: {e}")
            return False
        return True

    def _localize_factor(self, definition: Dict) -> Optional[str]:
        """
        将 JSON 因子定义转化为 Python BaseFactor 类文件。

        生成目录: backend/services/factor_engine/factors/external/
        """
        factor_id = definition["factor_id"]
        # 清理 factor_id 中的特殊字符
        safe_id = re.sub(r"[^a-zA-Z0-9_]", "_", factor_id)
        filename = f"{safe_id}.py"
        filepath = os.path.join(self._external_dir, filename)

        # 生成类名（PascalCase）
        class_name = "".join(
            word.capitalize() for word in safe_id.split("_") if word
        )

        params = definition.get("parameters", {})
        if isinstance(params, dict):
            params_str = json.dumps(params, ensure_ascii=False)
        else:
            params_str = "{}"

        required_fields = definition.get("required_data_fields", ["close"])
        if isinstance(required_fields, list):
            fields_str = json.dumps(required_fields)
        else:
            fields_str = '["close"]'

        dependencies = definition.get("dependencies", [])
        if isinstance(dependencies, list):
            deps_str = json.dumps(dependencies)
        else:
            deps_str = "[]"

        # 提取 calculate 方法体
        calc_code = definition.get("calculation_code", "")
        # 如果已经有 def calculate(...) 则缩进到类方法级别
        if "def calculate" in calc_code:
            indented_lines = []
            for line in calc_code.split("\n"):
                if line.strip().startswith("def calculate"):
                    indented_lines.append("    " + line)
                elif line.strip():
                    indented_lines.append("        " + line)
            method_code = "\n".join(indented_lines)
        else:
            method_code = f"    def calculate(self, data: pd.DataFrame) -> pd.Series:\n"
            for line in calc_code.strip().split("\n"):
                method_code += f"        {line}\n"
            method_code += f"        return result\n"

        code = f'''"""Cloud-synced factor: {definition.get("display_name", definition["name"])}"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata


class {class_name}(BaseFactor):
    """Auto-localized from cloud factor library."""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="{factor_id}",
            name="{definition["name"]}",
            display_name="{definition.get("display_name", definition["name"])}",
            description="""{definition.get("description", "")}""",
            category="{definition["category"]}",
            subcategory="{definition.get("subcategory", "")}",
            version="{definition.get("version", "1.0.0")}",
            author="{definition.get("author", "Cloud Sync")}",
            required_data_fields={fields_str},
            dependencies={deps_str},
        )

    def get_default_params(self):
        return {params_str}

{method_code}
'''

        try:
            os.makedirs(self._external_dir, exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(code)

            # 验证生成的文件可以编译
            with open(filepath, "r") as f:
                compile(f.read(), filepath, "exec")

            logger.info(f"[FactorSync] 本地化成功: {factor_id} → {filepath}")
            return filepath

        except Exception as e:
            logger.error(f"[FactorSync] 本地化失败 {factor_id}: {e}")
            # 清理不完整的文件
            if os.path.exists(filepath):
                os.remove(filepath)
            return None

    def _register_localized_factor(self, filepath: str, definition: Dict) -> bool:
        """注册本地化因子到 FactorRegistry"""
        try:
            from backend.services.factor_engine.factor_registry import registry
            import importlib.util

            module_name = f"cloud_factor_{definition['factor_id']}"
            spec = importlib.util.spec_from_file_location(module_name, filepath)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # 找到 BaseFactor 子类
                from backend.services.factor_engine.factor_base import BaseFactor
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (isinstance(attr, type)
                            and issubclass(attr, BaseFactor)
                            and attr is not BaseFactor):
                        registry.register(attr, override=True)
                        logger.info(
                            f"[FactorSync] 已注册: {definition['factor_id']} "
                            f"→ {attr_name}"
                        )
                        return True

            logger.warning(f"[FactorSync] 注册失败: 未找到 BaseFactor 子类 in {filepath}")
            return False

        except Exception as e:
            logger.error(f"[FactorSync] 注册异常 {definition.get('factor_id')}: {e}")
            return False

    def _upsert_cloud_factor(
        self, db, definition: Dict, source_repo: str
    ) -> None:
        """插入或更新 CloudFactorDefinition 记录"""
        from backend.database.models import CloudFactorDefinition

        fid = definition["factor_id"]
        existing = db.query(CloudFactorDefinition).filter(
            CloudFactorDefinition.factor_id == fid
        ).first()

        if existing:
            existing.name = definition["name"]
            existing.display_name = definition.get("display_name", definition["name"])
            existing.description = definition.get("description", "")
            existing.category = definition["category"]
            existing.subcategory = definition.get("subcategory", "")
            existing.calculation_code = definition.get("calculation_code", "")
            existing.parameters = definition.get("parameters")
            existing.required_data_fields = definition.get("required_data_fields")
            existing.dependencies = definition.get("dependencies")
            existing.version = definition.get("version", "1.0.0")
            existing.author = definition.get("author", "")
            existing.tags = definition.get("tags")
            existing.source_repo = source_repo
            existing.updated_at = datetime.now(timezone.utc)
        else:
            new_factor = CloudFactorDefinition(
                factor_id=fid,
                source_repo=source_repo,
                name=definition["name"],
                display_name=definition.get("display_name", definition["name"]),
                description=definition.get("description", ""),
                category=definition["category"],
                subcategory=definition.get("subcategory", ""),
                calculation_code=definition.get("calculation_code", ""),
                parameters=definition.get("parameters"),
                required_data_fields=definition.get("required_data_fields"),
                dependencies=definition.get("dependencies"),
                version=definition.get("version", "1.0.0"),
                author=definition.get("author", ""),
                tags=definition.get("tags"),
            )
            db.add(new_factor)

        db.commit()

    def _update_factor_status(
        self, db, factor_id: str, status: str,
        error_message: Optional[str] = None,
        localized_path: Optional[str] = None,
    ) -> None:
        """更新因子状态"""
        from backend.database.models import CloudFactorDefinition

        factor = db.query(CloudFactorDefinition).filter(
            CloudFactorDefinition.factor_id == factor_id
        ).first()

        if factor:
            factor.status = status
            if error_message:
                factor.error_message = error_message
            if localized_path:
                factor.localized = True
                factor.localized_path = localized_path
                factor.localized_at = datetime.now(timezone.utc)
            if status == "active":
                factor.localized = True
            db.commit()


# 模块级单例
factor_sync_service = FactorSyncService()
