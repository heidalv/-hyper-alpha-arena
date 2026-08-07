#!/usr/bin/env python3
"""临时脚本：运行数据库迁移"""
import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 运行迁移
from backend.database.migration_manager import run_all_migrations

if __name__ == "__main__":
    print("开始运行数据库迁移...")
    run_all_migrations()
    print("迁移完成！")
