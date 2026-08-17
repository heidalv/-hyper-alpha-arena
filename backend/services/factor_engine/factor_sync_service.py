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
        # [2026-08-14 P1-E5] 云端因子待验证目录：下划线前缀 → FactorLoader 按约定
        # 跳过，本地化后不会进入实盘计算；通过 promote_cloud_factor 显式晋升
        # （移入 external/ + 注册）后才生效。
        self._cloud_pending_dir = os.path.join(
            self._project_root,
            "backend", "services", "factor_engine", "factors", "_cloud_pending",
        )

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def sync_from_repo(self, config_id: int = None) -> Dict:
        """
        完整同步流程：下载 → 验证 → 本地化（候选，不注册）。

        Args:
            config_id: 指定同步配置 ID（None=全部启用的配置）

        Returns:
            同步结果摘要
        """
        # [2026-08-14 P1-E5] 总开关：安全加固完成前默认禁用（FACTOR_CLOUD_SYNC_ENABLED）。
        try:
            from backend.config import settings as _sync_s
            if not bool(getattr(_sync_s, "FACTOR_CLOUD_SYNC_ENABLED", False)):
                logger.warning(
                    "[FactorSync] 云同步已禁用（FACTOR_CLOUD_SYNC_ENABLED=false，安全加固默认关）"
                )
                return {"status": "skipped", "reason": "cloud sync disabled by FACTOR_CLOUD_SYNC_ENABLED"}
        except Exception:
            pass
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

                            # 本地化（写入 _cloud_pending，不注册、不置 active）
                            local_path = self._localize_factor(defn)
                            if local_path:
                                self._update_factor_status(
                                    db, defn["factor_id"], "candidate",
                                    localized_path=local_path,
                                )
                                total_localized += 1
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
                # [2026-08-14 P1-E5 修复] 本地化后状态=candidate（待验证），
                # 不再无条件置 active；文件落在 _cloud_pending（loader 不扫描），
                # 不注册进 Registry → 未经验证的云端因子不会进入实盘计算。
                # 晋升需显式调用 promote_cloud_factor()。
                factor.status = "candidate"
                db.commit()
                return {"status": "success", "factor_id": factor_id, "path": local_path,
                        "note": "candidate（待验证，未注册进实盘）；确认安全后调用 promote_cloud_factor"}

            return {"status": "error", "reason": "localization failed"}

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def promote_cloud_factor(self, factor_id: str, confirm: bool = False) -> Dict:
        """[2026-08-14 P1-E5] 显式晋升：把已验证的云端候选因子移入 external/ 并注册。

        - 仅当 confirm=True 时执行（人工确认），且重新过一遍安全校验；
        - 移文件（_cloud_pending → external/）→ 注册 Registry → 检查返回值 → active；
        - 任何一步失败都不置 active。
        """
        if not confirm:
            return {"status": "skipped", "reason": "promote requires confirm=True（显式确认）"}
        from backend.database.connection import SessionLocal
        from backend.database.models import CloudFactorDefinition

        with SessionLocal() as db:
            factor = db.query(CloudFactorDefinition).filter(
                CloudFactorDefinition.factor_id == factor_id
            ).first()
            if not factor:
                return {"status": "error", "reason": f"factor {factor_id} not found"}
            if not factor.localized or not factor.localized_path:
                return {"status": "error", "reason": "因子未本地化，先执行 localize"}

            # 重新安全校验（晋升点二次把关）
            if not self._validate_code(factor.calculation_code or ""):
                return {"status": "error", "reason": "security validation failed"}

            src = factor.localized_path
            if not os.path.exists(src):
                return {"status": "error", "reason": f"本地化文件不存在: {src}"}
            safe_id = re.sub(r"[^a-zA-Z0-9_]", "_", str(factor_id))
            dest = os.path.join(self._external_dir, f"{safe_id}.py")
            try:
                os.makedirs(self._external_dir, exist_ok=True)
                if os.path.exists(dest):
                    os.remove(dest)
                shutil.move(src, dest)
            except Exception as e:
                logger.error(f"[FactorSync] 晋升移文件失败 {factor_id}: {e}")
                return {"status": "error", "reason": f"move failed: {e}"}

            defn = {
                "factor_id": factor.factor_id,
                "name": factor.name,
                "display_name": factor.display_name or factor.name,
                "description": factor.description or "",
                "category": factor.category,
                "subcategory": factor.subcategory or "",
            }
            ok = self._register_localized_factor(dest, defn)
            if not ok:
                logger.error(f"[FactorSync] 晋升注册失败（状态保持 candidate）: {factor_id}")
                # 移回 pending，保持 candidate
                try:
                    shutil.move(dest, src)
                except Exception:
                    pass
                factor.status = "candidate"
                db.commit()
                return {"status": "error", "reason": "register failed, status kept candidate"}

            factor.localized_path = dest
            factor.status = "active"
            db.commit()
            logger.info(f"[FactorSync] 云端因子晋升 active: {factor_id} → {dest}")
            return {"status": "success", "factor_id": factor_id, "path": dest}

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
        """安全验证因子代码：黑名单 + 编译 + AST 白名单（code_safety）。"""
        code_lower = (code or "").lower()
        for pattern in _FORBIDDEN_PATTERNS:
            if pattern in code_lower:
                logger.warning(f"[FactorSync] 代码包含禁止模式: {pattern}")
                return False
        try:
            compile(code, "<factor_validation>", "exec")
        except SyntaxError as e:
            logger.warning(f"[FactorSync] 语法错误: {e}")
            return False
        # [2026-08-14 P1-F1] AST 白名单：禁 import/dunder/白名单外属性链与函数调用
        from backend.services.factor_engine.code_safety import ast_whitelist_check
        ok, reason = ast_whitelist_check(code)
        if not ok:
            logger.warning(f"[FactorSync] AST 白名单拒绝: {reason}")
            return False
        return True

    def _localize_factor(self, definition: Dict) -> Optional[str]:
        """
        将 JSON 因子定义转化为 Python BaseFactor 类文件。

        [2026-08-14 P1-E5/P1-F1 修复]
        - 生成目录改为 factors/_cloud_pending/（下划线前缀 → FactorLoader 跳过，
          本地化不等于上线；经 promote_cloud_factor 晋升后才进入 external/ 与 Registry）。
        - 所有元数据用 json.dumps 生成字符串字面量（修复裸插值注入面：含引号/
          换行的 name/description 等不再能逃逸出字符串）。
        - factor_id 拒绝路径分隔符与 ..（路径穿越）。
        """
        factor_id = str(definition.get("factor_id") or "")
        # 路径穿越防护：先拒，再清洗（清洗只影响合法字符集）
        if not factor_id or any(sep in factor_id for sep in ("/", "\\", "..")):
            logger.warning(f"[FactorSync] 拒绝非法 factor_id: {factor_id!r}")
            return None
        safe_id = re.sub(r"[^a-zA-Z0-9_]", "_", factor_id)
        if not safe_id:
            logger.warning(f"[FactorSync] factor_id 清洗后为空: {factor_id!r}")
            return None
        filename = f"{safe_id}.py"
        filepath = os.path.join(self._cloud_pending_dir, filename)

        # 生成类名（PascalCase）
        class_name = "".join(
            word.capitalize() for word in safe_id.split("_") if word
        ) or "CloudFactor"
        if not class_name[0].isalpha():
            class_name = "CloudFactor_" + class_name

        # ── 元数据字符串字面量：json.dumps 转义（含引号/换行/反斜杠安全）──
        def _q(value) -> str:
            return json.dumps(str(value or ""), ensure_ascii=False)

        # docstring 内嵌用「剥除外层引号」的转义内容：json.dumps 的输出自带外层
        # 引号，若值以引号结尾，其收尾引号会与 docstring 的三重引号终止符粘连
        # （`#""""`），导致 unterminated string literal。
        def _q_naked(value) -> str:
            return json.dumps(str(value or ""), ensure_ascii=False)[1:-1]

        fid_q = _q(factor_id)
        name_q = _q(definition.get("name"))
        display_q = _q(definition.get("display_name", definition.get("name")))
        display_naked = _q_naked(definition.get("display_name", definition.get("name")))
        desc_q = _q(definition.get("description", ""))
        cat_q = _q(definition.get("category"))
        subcat_q = _q(definition.get("subcategory", ""))
        ver_q = _q(definition.get("version", "1.0.0"))
        author_q = _q(definition.get("author", "Cloud Sync"))

        params = definition.get("parameters", {})
        params_str = json.dumps(params, ensure_ascii=False) if isinstance(params, dict) else "{}"
        required_fields = definition.get("required_data_fields", ["close"])
        fields_str = json.dumps(required_fields) if isinstance(required_fields, list) else '["close"]'
        dependencies = definition.get("dependencies", [])
        deps_str = json.dumps(dependencies) if isinstance(dependencies, list) else "[]"

        # 提取 calculate 方法体
        calc_code = definition.get("calculation_code", "")
        if "def calculate" in calc_code:
            indented_lines = []
            for line in calc_code.split("\n"):
                if line.strip().startswith("def calculate"):
                    indented_lines.append("    " + line)
                elif line.strip():
                    indented_lines.append("        " + line)
            method_code = "\n".join(indented_lines)
        else:
            method_code = (
                "    def calculate(self, data: pd.DataFrame) -> pd.Series:\n"
                + "".join(f"        {line}\n" for line in calc_code.strip().split("\n"))
                + "        return result\n"
            )

        # 模板分段拼接：元数据全部为 json 转义字面量；method_code（已过 AST 白名单）原样插入。
        code = (
            '"""Cloud-synced factor (pending validation): %s"""\n'
            "import pandas as pd\n"
            "import numpy as np\n"
            "from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata\n"
            "\n\n"
            "class %s(BaseFactor):\n"
            '    """Auto-localized from cloud factor library."""\n\n'
            "    def get_metadata(self) -> FactorMetadata:\n"
            "        return FactorMetadata(\n"
            "            factor_id=%s,\n"
            "            name=%s,\n"
            "            display_name=%s,\n"
            "            description=%s,\n"
            "            category=%s,\n"
            "            subcategory=%s,\n"
            "            version=%s,\n"
            "            author=%s,\n"
            "            required_data_fields=%s,\n"
            "            dependencies=%s,\n"
            "        )\n\n"
            "    def get_default_params(self):\n"
            "        return %s\n\n"
            "%s\n"
        ) % (
            display_naked, class_name, fid_q, name_q, display_q, desc_q, cat_q,
            subcat_q, ver_q, author_q, fields_str, deps_str, params_str,
            method_code,
        )

        try:
            os.makedirs(self._cloud_pending_dir, exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(code)

            # 验证生成的文件可以编译
            with open(filepath, "r", encoding="utf-8") as f:
                compile(f.read(), filepath, "exec")

            logger.info(f"[FactorSync] 本地化成功（待验证）: {factor_id} → {filepath}")
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

            # [2026-08-16 修复] 仓库迁移后 localized_path 指向旧路径（/Users/laobao/项目/...），
            # 文件实际已归档进 factors/_ai_gen_archive。缺失文件是常态（隔离设计：
            # 归档因子不自动进实盘注册表，须经冷池扫描+闸门晋升），降级为 info 跳过，
            # 不再刷 ERROR。
            if not os.path.isfile(filepath):
                logger.info(
                    f"[FactorSync] 本地化文件缺失，跳过注册（等待冷池扫描晋升）: {definition.get('factor_id')}"
                )
                return False

            _safe_mod = re.sub(r"[^a-zA-Z0-9_]", "_", str(definition.get("factor_id") or ""))
            module_name = f"cloud_factor_{_safe_mod or 'anon'}"
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
