"""
生产环境部署检查工具

用于上线前的全面检查，确保系统准备就绪
"""
import sys
import os
from typing import Dict, List, Tuple
from datetime import datetime

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class DeploymentChecker:
    """部署检查器"""
    
    def __init__(self):
        self.checks_passed = 0
        self.checks_failed = 0
        self.warnings = 0
        self.results = []
    
    def check_database_connection(self) -> Tuple[bool, str]:
        """检查数据库连接"""
        try:
            from backend.database.connection import get_db
            db = next(get_db())
            db.execute("SELECT 1")
            db.close()
            return True, "数据库连接正常"
        except Exception as e:
            return False, f"数据库连接失败: {e}"
    
    def check_database_tables(self) -> Tuple[bool, str]:
        """检查数据库表是否存在"""
        try:
            from backend.database.connection import get_db
            from backend.database.models import AIStrategy, StrategyMemory, PromptTrainingRecord
            
            db = next(get_db())
            
            # 检查关键表
            tables_to_check = [
                ("ai_strategies", AIStrategy),
                ("strategy_memories", StrategyMemory),
                ("prompt_training_records", PromptTrainingRecord),
            ]
            
            for table_name, model in tables_to_check:
                count = db.query(model).count()
                # 表能查询说明存在
            
            db.close()
            return True, "所有关键表已创建"
        except Exception as e:
            return False, f"数据库表检查失败: {e}"
    
    def check_database_indexes(self) -> Tuple[bool, str]:
        """检查数据库索引（建议性）"""
        try:
            from backend.database.connection import engine
            
            # 这里只是检查是否可以查询索引信息
            with engine.connect() as conn:
                result = conn.execute("""
                    SELECT indexname FROM pg_indexes 
                    WHERE tablename IN ('ai_strategies', 'strategy_memories', 'prompt_training_records')
                    LIMIT 5
                """)
                indexes = [row[0] for row in result]
            
            if len(indexes) > 0:
                return True, f"发现 {len(indexes)} 个索引"
            else:
                return True, "建议运行 performance_optimizer.py 创建索引（警告）"
        except Exception as e:
            return True, f"索引检查跳过: {e}"
    
    def check_api_routes(self) -> Tuple[bool, str]:
        """检查API路由是否注册"""
        try:
            from backend.main import app
            
            routes = [route.path for route in app.routes]
            
            required_routes = [
                "/api/ai-strategies",
                "/api/prompt-training",
            ]
            
            missing = [r for r in required_routes if not any(r in route for route in routes)]
            
            if missing:
                return False, f"缺少路由: {', '.join(missing)}"
            
            return True, f"已注册 {len(routes)} 个路由"
        except Exception as e:
            return False, f"API路由检查失败: {e}"
    
    def check_services(self) -> Tuple[bool, str]:
        """检查核心服务是否可导入"""
        try:
            from backend.services.strategy_coordinator import StrategyCoordinator
            from backend.services.ai_decision_service import call_ai_for_decision
            
            return True, "核心服务导入正常 (StrategyCoordinator + ai_decision_service)"
        except Exception as e:
            return False, f"服务导入失败: {e}"
    
    def check_environment_variables(self) -> Tuple[bool, str]:
        """检查环境变量配置"""
        import os
        
        required_vars = [
            "DATABASE_URL",
        ]
        
        optional_vars = [
            "OPENAI_API_KEY",
            "REDIS_URL",
        ]
        
        missing_required = [v for v in required_vars if not os.getenv(v)]
        missing_optional = [v for v in optional_vars if not os.getenv(v)]
        
        if missing_required:
            return False, f"缺少必需环境变量: {', '.join(missing_required)}"
        
        if missing_optional:
            return True, f"建议配置可选变量: {', '.join(missing_optional)}"
        
        return True, "环境变量配置完整"
    
    def check_file_structure(self) -> Tuple[bool, str]:
        """检查关键文件是否存在"""
        required_files = [
            "backend/services/ai_strategy_engine.py",
            "backend/services/prompt_training_system.py",
            "backend/api/ai_strategy_routes.py",
            "backend/api/prompt_training_routes.py",
            "backend/utils/monitoring.py",
            "backend/utils/performance_optimizer.py",
            "backend/tests/test_ai_strategy_integration.py",
        ]
        
        base_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        
        missing = []
        for file_path in required_files:
            full_path = os.path.join(base_path, file_path)
            if not os.path.exists(full_path):
                missing.append(file_path)
        
        if missing:
            return False, f"缺少文件: {', '.join(missing)}"
        
        return True, f"所有关键文件已就位 ({len(required_files)}个)"
    
    def check_dependencies(self) -> Tuple[bool, str]:
        """检查Python依赖包"""
        try:
            import fastapi
            import sqlalchemy
            import pytest
            
            return True, "核心依赖包已安装"
        except ImportError as e:
            return False, f"缺少依赖包: {e}"
    
    def check_logs_directory(self) -> Tuple[bool, str]:
        """检查日志目录"""
        base_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        logs_dir = os.path.join(base_path, "logs")
        
        if not os.path.exists(logs_dir):
            try:
                os.makedirs(logs_dir)
                return True, "日志目录已创建"
            except Exception as e:
                return False, f"无法创建日志目录: {e}"
        
        return True, "日志目录已存在"
    
    def check_frontend_build(self) -> Tuple[bool, str]:
        """检查前端文件"""
        base_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        
        frontend_files = [
            "frontend/app/components/atas-v2/AiStrategyList.tsx",
            "frontend/app/components/atas-v2/AiStrategyWizard.tsx",
            "frontend/app/components/atas-v2/AiStrategyDetail.tsx",
            "frontend/app/components/atas-v2/PromptTrainingConsole.tsx",
        ]
        
        missing = []
        for file_path in frontend_files:
            full_path = os.path.join(base_path, file_path)
            if not os.path.exists(full_path):
                missing.append(file_path)
        
        if missing:
            return False, f"缺少前端文件: {', '.join(missing)}"
        
        return True, f"前端文件已就位 ({len(frontend_files)}个)"
    
    def run_all_checks(self) -> Dict:
        """运行所有检查"""
        checks = [
            ("数据库连接", self.check_database_connection),
            ("数据库表结构", self.check_database_tables),
            ("数据库索引", self.check_database_indexes),
            ("API路由注册", self.check_api_routes),
            ("核心服务", self.check_services),
            ("环境变量", self.check_environment_variables),
            ("文件结构", self.check_file_structure),
            ("依赖包", self.check_dependencies),
            ("日志目录", self.check_logs_directory),
            ("前端文件", self.check_frontend_build),
        ]
        
        print("=" * 70)
        print("🔍 生产环境部署检查")
        print("=" * 70)
        print(f"检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print()
        
        for check_name, check_func in checks:
            try:
                passed, message = check_func()
                
                if passed:
                    if "警告" in message or "建议" in message:
                        print(f"⚠️  {check_name}: {message}")
                        self.warnings += 1
                    else:
                        print(f"✅ {check_name}: {message}")
                        self.checks_passed += 1
                else:
                    print(f"❌ {check_name}: {message}")
                    self.checks_failed += 1
                
                self.results.append({
                    "check": check_name,
                    "passed": passed,
                    "message": message,
                })
            except Exception as e:
                print(f"❌ {check_name}: 检查出错 - {e}")
                self.checks_failed += 1
                self.results.append({
                    "check": check_name,
                    "passed": False,
                    "message": f"检查出错: {e}",
                })
        
        print()
        print("=" * 70)
        print("📊 检查结果汇总")
        print("=" * 70)
        print(f"✅ 通过: {self.checks_passed}")
        print(f"⚠️  警告: {self.warnings}")
        print(f"❌ 失败: {self.checks_failed}")
        print()
        
        total = self.checks_passed + self.checks_failed + self.warnings
        success_rate = (self.checks_passed / total * 100) if total > 0 else 0
        
        print(f"总体通过率: {success_rate:.1f}%")
        print()
        
        if self.checks_failed == 0:
            print("🎉 所有关键检查通过，系统可以部署！")
        else:
            print("⚠️  存在失败项，请修复后再部署")
        
        print("=" * 70)
        
        return {
            "passed": self.checks_passed,
            "failed": self.checks_failed,
            "warnings": self.warnings,
            "success_rate": success_rate,
            "ready_to_deploy": self.checks_failed == 0,
            "results": self.results,
        }


def generate_deployment_checklist():
    """生成部署检查清单"""
    checklist = """
# 🚀 生产环境部署检查清单

## 部署前检查

### 1. 环境准备
- [ ] Python 3.12+ 已安装
- [ ] PostgreSQL 14+ 已安装并运行
- [ ] Node.js 18+ 已安装（前端）
- [ ] 环境变量已配置

### 2. 数据库准备
- [ ] 数据库已创建
- [ ] 迁移脚本已执行
- [ ] 索引已创建（运行 performance_optimizer.py）
- [ ] 数据库备份已配置

### 3. 代码准备
- [ ] 代码已拉取到生产服务器
- [ ] 依赖包已安装（pip install -r requirements.txt）
- [ ] 前端已构建（npm run build）
- [ ] 测试已通过（pytest）

### 4. 配置检查
- [ ] 日志目录已创建
- [ ] 日志级别已配置（生产环境建议 INFO）
- [ ] 监控端点已启用
- [ ] 告警规则已配置

### 5. 安全检查
- [ ] API密钥已加密存储
- [ ] 数据库密码已更新
- [ ] HTTPS已配置
- [ ] 防火墙规则已设置

## 部署步骤

### Step 1: 停止旧服务（如果存在）
```bash
# 停止旧进程
pkill -f "python launcher.py"
```

### Step 2: 拉取最新代码
```bash
git pull origin main
```

### Step 3: 更新依赖
```bash
pip install -r requirements.txt --upgrade
```

### Step 4: 运行数据库迁移
```bash
# 系统启动时会自动运行
# 或手动运行检查
python -c "from backend.database.migration_manager import run_all_migrations; run_all_migrations()"
```

### Step 5: 应用性能优化
```bash
python backend/utils/performance_optimizer.py
```

### Step 6: 运行测试
```bash
pytest backend/tests/test_ai_strategy_integration.py -v
```

### Step 7: 启动服务
```bash
# 前台启动（测试）
python launcher.py

# 后台启动（生产）
nohup python launcher.py > logs/launcher.log 2>&1 &
```

### Step 8: 验证部署
```bash
# 检查健康状态
curl http://localhost:8000/api/monitoring/health

# 检查API端点
curl http://localhost:8000/api/ai-strategies

# 检查前端
curl http://localhost:5173
```

## 部署后监控

### 1. 日志监控
```bash
# 查看系统日志
tail -f logs/ai_strategy_system.log

# 查看错误日志
tail -f logs/ai_strategy_errors.log
```

### 2. 性能监控
- 访问: http://localhost:8000/api/monitoring/metrics
- 关注: 策略执行时间、API响应时间

### 3. 告警配置
- 确认告警渠道已配置
- 测试告警是否能正常发送

## 回滚计划

如果部署出现问题，执行以下步骤回滚：

### Step 1: 停止新服务
```bash
pkill -f "python launcher.py"
```

### Step 2: 恢复旧版本代码
```bash
git checkout <previous_commit>
```

### Step 3: 恢复数据库（如有schema变更）
```bash
# 从备份恢复
psql -U your_user -d your_db < backup.sql
```

### Step 4: 重启旧服务
```bash
nohup python launcher.py > logs/launcher.log 2>&1 &
```

## 灰度发布建议

1. **第一阶段**（10%流量）
   - 选择1-2个测试账户
   - 开启AI策略功能
   - 观察24小时

2. **第二阶段**（50%流量）
   - 扩展到50%账户
   - 观察性能指标
   - 收集用户反馈

3. **第三阶段**（100%流量）
   - 全量开放
   - 持续监控
   - 优化调整

## 应急联系人

- **系统负责人**: [姓名] - [电话]
- **数据库DBA**: [姓名] - [电话]
- **运维工程师**: [姓名] - [电话]

---

**检查人**: ___________  
**检查日期**: ___________  
**部署状态**: [ ] 通过 [ ] 失败  
"""
    return checklist


if __name__ == "__main__":
    # 运行部署检查
    checker = DeploymentChecker()
    result = checker.run_all_checks()
    
    # 生成检查清单
    if result["ready_to_deploy"]:
        print("\n💡 提示: 运行以下命令查看详细部署步骤：")
        print("   python -c \"from backend.utils.deployment_checker import generate_deployment_checklist; print(generate_deployment_checklist())\"")
