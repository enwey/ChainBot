#!/bin/bash
echo "=================================================="
echo "Investment Automation 服务启动"
echo "=================================================="

echo "正在检查并拉起监控引擎与 Web 服务..."
export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"
python3 -u main.py
