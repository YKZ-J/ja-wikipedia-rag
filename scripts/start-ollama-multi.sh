#!/usr/bin/env bash
# Ollama をポート 11434 / 11435 / 11436 の3インスタンスで起動する
# Usage: ./scripts/start-ollama-multi.sh [start|stop|status]

set -euo pipefail

PORTS=(11434 11435 11436 11437 11438 11439)
MODEL="nomic-embed-text"
LOG_DIR="/tmp/ollama-multi"
mkdir -p "$LOG_DIR"

start() {
    for port in "${PORTS[@]}"; do
        if lsof -iTCP:"$port" -sTCP:LISTEN &>/dev/null; then
            echo "[port $port] 既に起動中 → スキップ"
            continue
        fi
        echo "[port $port] 起動中..."
        OLLAMA_HOST="127.0.0.1:$port" \
            ollama serve \
            > "$LOG_DIR/ollama_$port.log" 2>&1 &
        echo $! > "$LOG_DIR/ollama_$port.pid"
    done

    # 起動待ち
    echo "起動待ち (5秒)..."
    sleep 5

    # nomic-embed-text をプリロード
    for port in "${PORTS[@]}"; do
        echo "[port $port] $MODEL をプリロード..."
        curl -s -o /dev/null \
            "http://127.0.0.1:$port/api/pull" \
            -d "{\"model\":\"$MODEL\"}" || true
    done

    status
}

stop() {
    for port in "${PORTS[@]}"; do
        pidfile="$LOG_DIR/ollama_$port.pid"
        if [[ -f "$pidfile" ]]; then
            pid=$(cat "$pidfile")
            if kill -0 "$pid" 2>/dev/null; then
                kill "$pid" && echo "[port $port] 停止 (PID $pid)"
            fi
            rm -f "$pidfile"
        else
            # pid ファイルがない場合はポートで検索
            pid=$(lsof -ti TCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
            if [[ -n "$pid" ]]; then
                kill "$pid" && echo "[port $port] 停止 (PID $pid)"
            else
                echo "[port $port] 起動していません"
            fi
        fi
    done
}

status() {
    echo "--- Ollama インスタンス状態 ---"
    for port in "${PORTS[@]}"; do
        if curl -s --max-time 2 "http://127.0.0.1:$port/api/tags" &>/dev/null; then
            echo "[port $port] ✓ 起動中"
        else
            echo "[port $port] ✗ 停止中"
        fi
    done
}

case "${1:-start}" in
    start)  start  ;;
    stop)   stop   ;;
    status) status ;;
    *)
        echo "Usage: $0 [start|stop|status]"
        exit 1
        ;;
esac
