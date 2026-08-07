"""
Backend package - 添加项目根目录到sys.path以支持相对导入
"""
import sys
import os

# 确保backend目录在Python路径中
_backend_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_backend_dir)

if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# 也添加backend目录本身
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
