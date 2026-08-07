#!/usr/bin/env python
"""
生产环境启动脚本

使用 Gunicorn 启动 FastAPI 应用
"""
import os
import sys
import subprocess
from pathlib import Path


def check_environment():
    """检查环境是否准备就绪"""
    print("🔍 检查环境...")
    
    # 检查Python版本
    if sys.version_info < (3, 10):
        print("❌ Python版本必须 >= 3.10")
        return False
    
    # 检查必需的包
    try:
        import uvicorn
        import fastapi
        import sqlalchemy
    except ImportError as e:
        print(f"❌ 缺少必需的包: {e}")
        print("请运行: pip install -r requirements.txt")
        return False
    
    # 检查日志目录
    logs_dir = Path("logs")
    if not logs_dir.exists():
        print("📁 创建日志目录...")
        logs_dir.mkdir(parents=True)
    
    print("✅ 环境检查通过")
    return True


def start_production():
    """启动生产环境服务"""
    print("🚀 启动生产环境服务...")
    
    # 设置环境变量
    os.environ["ENVIRONMENT"] = "production"
    
    # 使用uvicorn启动（生产环境建议使用gunicorn+uvicorn workers）
    cmd = [
        "uvicorn",
        "backend.main:app",
        "--host", "0.0.0.0",
        "--port", "8000",
        "--workers", "4",
        "--log-level", "info",
        "--access-log",
    ]
    
    print(f"执行命令: {' '.join(cmd)}")
    subprocess.run(cmd)


def start_development():
    """启动开发环境服务"""
    print("🛠️  启动开发环境服务...")
    
    os.environ["ENVIRONMENT"] = "development"
    
    cmd = [
        "uvicorn",
        "backend.main:app",
        "--host", "127.0.0.1",
        "--port", "8000",
        "--reload",
        "--log-level", "debug",
    ]
    
    print(f"执行命令: {' '.join(cmd)}")
    subprocess.run(cmd)


def run_migrations():
    """运行数据库迁移"""
    print("🗄️  运行数据库迁移...")
    
    try:
        from backend.database.migration_manager import run_all_migrations
        run_all_migrations()
        print("✅ 迁移完成")
    except Exception as e:
        print(f"❌ 迁移失败: {e}")
        return False
    
    return True


def run_tests():
    """运行测试"""
    print("🧪 运行测试...")
    
    cmd = [
        "pytest",
        "backend/tests/test_ai_strategy_integration.py",
        "-v",
        "--tb=short",
    ]
    
    result = subprocess.run(cmd)
    return result.returncode == 0


def show_help():
    """显示帮助信息"""
    help_text = """
🚀 Hyper-Alpha-Arena 启动脚本

用法:
    python start.py [命令]

命令:
    prod        - 启动生产环境服务 (端口: 8000)
    dev         - 启动开发环境服务 (端口: 8000, 热重载)
    migrate     - 运行数据库迁移
    test        - 运行测试
    check       - 检查部署环境
    help        - 显示此帮助信息

示例:
    python start.py dev          # 开发环境
    python start.py prod         # 生产环境
    python start.py migrate      # 运行迁移
    python start.py test         # 运行测试

环境变量:
    ENVIRONMENT     - 环境类型 (development/production/test)
    DATABASE_URL    - 数据库连接URL
    OPENAI_API_KEY  - OpenAI API密钥
    SECRET_KEY      - 应用密钥
"""
    print(help_text)


def main():
    """主函数"""
    if len(sys.argv) < 2:
        show_help()
        return
    
    command = sys.argv[1].lower()
    
    if command == "help":
        show_help()
    
    elif command == "check":
        from backend.utils.deployment_checker import DeploymentChecker
        checker = DeploymentChecker()
        checker.run_all_checks()
    
    elif command == "migrate":
        if not check_environment():
            sys.exit(1)
        if not run_migrations():
            sys.exit(1)
    
    elif command == "test":
        if not check_environment():
            sys.exit(1)
        if not run_tests():
            sys.exit(1)
    
    elif command == "dev":
        if not check_environment():
            sys.exit(1)
        start_development()
    
    elif command == "prod":
        if not check_environment():
            sys.exit(1)
        
        # 生产环境先运行检查
        print("\n🔍 运行部署检查...")
        from backend.utils.deployment_checker import DeploymentChecker
        checker = DeploymentChecker()
        result = checker.run_all_checks()
        
        if not result["ready_to_deploy"]:
            print("\n❌ 部署检查未通过，请先修复问题")
            sys.exit(1)
        
        start_production()
    
    else:
        print(f"❌ 未知命令: {command}")
        show_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
