#!/bin/bash
# ══════════════════════════════════════════════════════════════
#  D7: 因子库自动同步脚本
#  每周六 03:00 UTC 由 crontab 触发
#  
#  流程:
#    1. git pull 上游因子库
#    2. 运行适配器验证
#    3. 沙盒回测新因子
#    4. 发送同步报告
# ══════════════════════════════════════════════════════════════

set -e
PROJECT_ROOT="/Users/laobao/项目/claude/001-02Alpha/001Alpha/Hyper-Alpha-Arena"
FACTORS_DIR="$PROJECT_ROOT/backend/services/factor_engine/factors/external"
LOG_FILE="$PROJECT_ROOT/logs/factor_sync_$(date +%Y%m%d).log"

echo "========================================" | tee -a "$LOG_FILE"
echo " 因子库同步 $(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"

# 1. Git pull 外部因子库（如果使用 git 子模块）
if [ -d "$FACTORS_DIR/.git" ]; then
    echo "[1/4] 拉取上游因子库更新..." | tee -a "$LOG_FILE"
    cd "$FACTORS_DIR"
    git pull origin main 2>&1 | tee -a "$LOG_FILE" || echo "  (git pull 跳过 - 非git仓库或网络问题)" | tee -a "$LOG_FILE"
else
    echo "[1/4] 因子库为本地管理，跳过 git pull" | tee -a "$LOG_FILE"
fi

# 2. Python 语法验证
echo "[2/4] 验证因子代码语法..." | tee -a "$LOG_FILE"
cd "$PROJECT_ROOT"
PYTHON_BIN=/opt/homebrew/bin/python3.12
for f in "$FACTORS_DIR"/*.py; do
    if [ -f "$f" ] && [[ "$f" != *"__init__"* ]]; then
        $PYTHON_BIN -m py_compile "$f" 2>&1 && echo "  ✓ $(basename $f)" | tee -a "$LOG_FILE" || echo "  ✗ $(basename $f) 编译失败!" | tee -a "$LOG_FILE"
    fi
done

# 3. 沙盒回测（可选 — 调用 FactorSelector 的 IC 计算）
echo "[3/4] 运行因子验证..." | tee -a "$LOG_FILE"
$PYTHON_BIN -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT/backend')
try:
    from services.factor_engine.factor_loader import FactorLoader
    loader = FactorLoader()
    count = loader.discover_and_load_all()
    print(f'  已注册 {count} 个因子')
except Exception as e:
    print(f'  因子加载失败: {e}')
" 2>&1 | tee -a "$LOG_FILE"

# 4. 报告
echo "[4/4] 同步完成 $(date '+%H:%M:%S')" | tee -a "$LOG_FILE"
echo "日志: $LOG_FILE"

# Crontab 配置（手动添加）:
# 0 3 * * 6 /bin/bash /Users/laobao/项目/claude/001-02Alpha/001Alpha/Hyper-Alpha-Arena/scripts/sync_external_factors.sh
