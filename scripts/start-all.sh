#!/usr/bin/env bash
# 一键启动 Hyper-Alpha-Arena 开发栈（自检 + 清僵尸 + 启动 + 验收）
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/dev-stack.sh" up "$@"
