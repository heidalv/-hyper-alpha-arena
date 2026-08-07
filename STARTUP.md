# 🚀 Heidalv-Alpha-Arena 启动指南

## ⚡ 快速启动（3步搞定）

### 方法1：一键启动（最简单）

```bash
fix_and_start.bat
```

这个脚本会自动：
- ✅ 安装所有 Python 依赖
- ✅ 启动后端服务（FastAPI）
- ✅ 打开浏览器

---

### 方法2：快速启动（已安装依赖）

```bash
quick_start.bat
```

如果已经安装过依赖，使用这个更快。

---

### 方法3：手动启动

```bash
# 1. 安装依赖（首次使用）
install_dependencies.bat

# 2. 启动后端（会打开GUI启动器）
python launcher.py

# 3. 访问前端
浏览器打开: http://localhost:5173
```

---

## 🌐 访问地址

| 服务 | 地址 | 说明 |
|------|------|------|
| 前端界面 | http://localhost:5173 | React前端应用 |
| 后端API | http://localhost:8000 | FastAPI服务 |
| API文档 | http://localhost:8000/docs | Swagger文档 |
| 数据库 | data/alpha_arena.db | SQLite数据库文件 |

---

## 🔧 故障排查

### 问题1: 页面一直显示"加载 ATAS V2 策略中心..."

**原因**: 后端服务未启动

**解决方案**:
```bash
# 运行诊断
diagnose.bat

# 如果后端未运行，执行
python launcher.py
# 或
quick_start.bat
```

---

### 问题2: 页面显示红色警告"后端服务未启动"

**原因**: 后端 FastAPI 服务不在线

**解决方案**:
1. 运行 `quick_start.bat`
2. 或手动运行 `python launcher.py`
3. 等待 3-5 秒后刷新浏览器

---

### 问题3: 提示"缺少必要的Python依赖包"

**原因**: FastAPI、SQLAlchemy 等包未安装

**解决方案**:
```bash
# 方法1：一键安装并启动
fix_and_start.bat

# 方法2：只安装依赖
install_dependencies.bat
```

---

### 问题4: 数据一直显示"加载中..."

**可能原因**:
1. 后端服务未启动
2. 网络请求超时

**解决方案**:
```bash
# 1. 运行诊断
diagnose.bat

# 2. 检查诊断结果：
# - 如果"后端服务未运行" → 运行 python launcher.py
# - 如果"缺少依赖" → 运行 install_dependencies.bat

# 3. 前端已优化为10秒超时，会显示明确错误信息
```

---

## 📋 工具脚本说明

| 脚本 | 用途 | 何时使用 |
|------|------|----------|
| `fix_and_start.bat` | 一键修复并启动 | 首次使用或遇到问题时 |
| `quick_start.bat` | 快速启动 | 日常使用 |
| `install_dependencies.bat` | 安装Python依赖 | 首次使用或依赖缺失 |
| `diagnose.bat` | 系统诊断 | 遇到问题时排查 |
| `launcher.py` | GUI启动器 | 手动启动后端 |

---

## 💡 提示

### 首次使用流程
1. 双击 `fix_and_start.bat`
2. 等待依赖安装（约1-3分钟）
3. 后端服务自动启动
4. 浏览器自动打开前端

### 日常使用流程
1. 双击 `quick_start.bat`
2. 等待后端启动（约5秒）
3. 访问 http://localhost:5173

### 停止服务
- 关闭后端服务的命令窗口即可

### 数据位置
- 数据库文件：`data/alpha_arena.db`
- 快照数据库：`data/alpha_snapshots.db`

---

## 🎯 系统要求

- Python 3.10 或更高版本
- Node.js（前端已启动则无需）
- Windows 10/11（批处理脚本）

---

## ⚙️ 技术栈

- **后端**: FastAPI + Uvicorn
- **数据库**: SQLite 3
- **前端**: React + Vite
- **ORM**: SQLAlchemy

---

## 📞 获取帮助

如果以上方法都无法解决问题：

1. 运行 `diagnose.bat` 获取系统状态
2. 检查后端窗口的错误信息
3. 按 F12 打开浏览器控制台查看 Network 标签
4. 查看错误日志
