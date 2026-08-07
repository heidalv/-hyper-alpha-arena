#!/bin/bash
# 启动脚本 - 确保加密密钥持久化

set -e

echo "=== 初始化加密密钥持久化 ==="

# 从环境变量获取密钥
KEY="${BINANCE_ENCRYPTION_KEY:-${HYPERLIQUID_ENCRYPTION_KEY:-}"

if [ -z "$KEY" ]; then
    echo "⚠️  警告: 未找到加密密钥环境变量"
    echo "将生成临时密钥（不推荐生产环境）"
fi

# 确保数据目录存在
mkdir -p /app/data

# 持久化密钥文件
if [ -n "$KEY" ]; then
    echo "$KEY" > /app/data/.encryption_key
    echo "✅ 加密密钥已持久化到 /app/data/.encryption_key"
else
    # 如果没有密钥，生成一个新的
    if [ ! -f /app/data/.encryption_key ]; then
        python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > /app/data/.encryption_key
        echo "✅ 已生成新的加密密钥"
    fi
fi

echo "=== 启动应用服务 ==="
# 启动FastAPI应用
exec python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
