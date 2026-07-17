#!/bin/bash
# x-unyou-webapp 起動スクリプト
# 使い方: bash ~/x-unyou-webapp/start.sh

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
FLASK_LOG="/tmp/flask-xybboost.log"
SERVEO_LOG="/tmp/serveo.log"

# Flask が起動していなければ起動
if ! lsof -ti:5050 > /dev/null 2>&1; then
    echo "Flask を起動中..."
    cd "$APP_DIR"
    nohup python3 -m flask --app wsgi run --host=0.0.0.0 --port=5050 > "$FLASK_LOG" 2>&1 &
    FLASK_PID=$!
    echo "Flask PID: $FLASK_PID"
    sleep 3
else
    echo "Flask はすでに起動しています (PID: $(lsof -ti:5050))"
fi

# Serveo トンネルを起動
echo "Serveo トンネルを起動中..."
nohup ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -R xybboost:80:localhost:5050 serveo.net > "$SERVEO_LOG" 2>&1 &
SERVEO_PID=$!
echo "Serveo PID: $SERVEO_PID"

echo ""
echo "起動完了！"
echo "ローカル:   http://localhost:5050"
echo "インターネット: https://xybboost.serveo.net"
echo ""
echo "ログ確認:"
echo "  tail -f $FLASK_LOG"
echo "  tail -f $SERVEO_LOG"
