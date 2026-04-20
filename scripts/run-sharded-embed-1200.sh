#!/usr/bin/env bash
set -euo pipefail

# 1200/120 固定の shard 実行ヘルパー
# Usage:
#   scripts/run-sharded-embed-1200.sh start
#   scripts/run-sharded-embed-1200.sh status
#   scripts/run-sharded-embed-1200.sh stop

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT_DIR/logs/sharded-1200"
PID_DIR="$ROOT_DIR/logs/sharded-1200/pids"
PARQUET_DIR="$ROOT_DIR/parquet_output"

SHARD_COUNT="${SHARD_COUNT:-4}"
EMBEDDING_MODEL="${EMBEDDING_MODEL:-cl-nagoya/ruri-v3-30m}"
EMBEDDING_BATCH_SIZE="${EMBEDDING_BATCH_SIZE:-32}"
EMBEDDING_MAX_LENGTH="${EMBEDDING_MAX_LENGTH:-512}"
WORKERS_PER_SHARD="${WORKERS_PER_SHARD:-2}"
FILES_PER_SHARD="${FILES_PER_SHARD:-1}"
CHUNK_SIZE="${CHUNK_SIZE:-16}"
DB_BATCH_SIZE="${DB_BATCH_SIZE:-300}"
COPY_BULK="${COPY_BULK:-1}"
DB_TUNING="${DB_TUNING:-1}"
CONFLICT_MODE="${CONFLICT_MODE:-ignore}"
# 既存 .parquet.done を無視して再処理する（1200/120 の再移行が目的）
USE_SKIP_PROCESSED="${USE_SKIP_PROCESSED:-0}"

start() {
  mkdir -p "$LOG_DIR" "$PID_DIR"
  cd "$ROOT_DIR"

  for idx in $(seq 0 $((SHARD_COUNT - 1))); do
    pid_file="$PID_DIR/worker_${idx}.pid"
    if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
      echo "[worker $idx] already running pid=$(cat "$pid_file")"
      continue
    fi

    ts="$(date +%Y%m%d_%H%M%S)"
    log_file="$LOG_DIR/worker_${idx}_${ts}.log"

    echo "[worker $idx] start (engine=ruri-v3-30m workers=$WORKERS_PER_SHARD chunk_size=$CHUNK_SIZE db_batch=$DB_BATCH_SIZE) -> $log_file"
    skip_flag=""
    if [[ "$USE_SKIP_PROCESSED" == "1" ]]; then
      skip_flag="--skip-processed"
    fi
    copy_flag=""
    if [[ "$COPY_BULK" != "1" ]]; then
      copy_flag="--no-copy-bulk"
    fi
    tuning_flag=""
    if [[ "$DB_TUNING" != "1" ]]; then
      tuning_flag="--no-db-tuning"
    fi
    (
      set -a
      source .env.local
      set +a
      export TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM:-true}"
      export OMP_NUM_THREADS="${OMP_NUM_THREADS:-4}"
      export MKL_NUM_THREADS="${MKL_NUM_THREADS:-4}"
      exec .venv/bin/python -u python/embed_and_upload.py \
        --parquet "$PARQUET_DIR" \
        --embedding-model "$EMBEDDING_MODEL" \
        --embedding-batch-size "$EMBEDDING_BATCH_SIZE" \
        --embedding-max-length "$EMBEDDING_MAX_LENGTH" \
        --chunk-size "$CHUNK_SIZE" \
        --workers "$WORKERS_PER_SHARD" \
        --files "$FILES_PER_SHARD" \
        --target-table documents_v2 \
        --chunk-chars 1200 \
        --overlap-chars 120 \
        --min-chunk-chars 120 \
        --db-batch-size "$DB_BATCH_SIZE" \
        --conflict-mode "$CONFLICT_MODE" \
        --shard-index "$idx" \
        --shard-count "$SHARD_COUNT" \
        $skip_flag \
        $copy_flag \
        $tuning_flag
    ) > "$log_file" 2>&1 &

    echo $! > "$pid_file"
  done

  status
}

status() {
  echo "--- sharded 1200 status ---"
  for idx in $(seq 0 $((SHARD_COUNT - 1))); do
    pid_file="$PID_DIR/worker_${idx}.pid"
    if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
      pid="$(cat "$pid_file")"
      latest_log="$(ls -1t "$LOG_DIR"/worker_${idx}_*.log 2>/dev/null | head -n 1 || true)"
      echo "[worker $idx] running pid=$pid log=${latest_log:-N/A}"
    else
      echo "[worker $idx] stopped"
    fi
  done
}

stop() {
  for idx in $(seq 0 $((SHARD_COUNT - 1))); do
    pid_file="$PID_DIR/worker_${idx}.pid"
    if [[ -f "$pid_file" ]]; then
      pid="$(cat "$pid_file")"
      if kill -0 "$pid" 2>/dev/null; then
        kill "$pid"
        echo "[worker $idx] stopped pid=$pid"
      fi
      rm -f "$pid_file"
    else
      echo "[worker $idx] no pid file"
    fi
  done
}

case "${1:-start}" in
  start)
    start
    ;;
  status)
    status
    ;;
  stop)
    stop
    ;;
  *)
    echo "Usage: SHARD_COUNT=4 WORKERS_PER_SHARD=1 DB_BATCH_SIZE=300 EMBEDDING_MODEL=cl-nagoya/ruri-v3-30m $0 [start|status|stop]"
    exit 1
    ;;
esac
