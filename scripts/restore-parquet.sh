#!/usr/bin/env bash
# parquet_output バックアップから復元するスクリプト
#
# 使い方:
#   # 通常の tar.gz から復元
#   bash scripts/restore-parquet.sh parquet_output_20260312.tar.gz
#
#   # 分割ファイルから復元
#   bash scripts/restore-parquet.sh parquet_output_20260312_part_*

set -euo pipefail
cd "$(dirname "$0")/.."

LOG="/tmp/restore_parquet.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S JST')] $*" | tee -a "$LOG"; }

if [[ $# -eq 0 ]]; then
  echo "Usage: bash scripts/restore-parquet.sh <backup.tar.gz>"
  echo "       bash scripts/restore-parquet.sh <part_aa> <part_ab> ..."
  exit 1
fi

log "=== parquet_output 復元 ==="
log ""

FIRST_ARG="$1"

# 分割ファイルかどうか判定（複数引数 or "_part_" を含む場合）
if [[ $# -gt 1 ]] || [[ "$FIRST_ARG" == *"_part_"* ]]; then
  log "▶ 分割ファイルを結合して復元します..."
  BASENAME=$(echo "$FIRST_ARG" | sed 's/_part_.*/.tar.gz/')
  MERGED="${BASENAME}"

  if [[ $# -gt 1 ]]; then
    cat "$@" > "$MERGED"
  else
    # _part_aa, _part_ab, ... をパターンで結合
    PREFIX=$(echo "$FIRST_ARG" | sed 's/_part_.*//')
    cat "${PREFIX}_part_"* > "$MERGED"
  fi

  log "  結合ファイル: $MERGED ($(du -sh "$MERGED" | cut -f1))"
  ARCHIVE="$MERGED"
else
  ARCHIVE="$FIRST_ARG"
fi

if [[ ! -f "$ARCHIVE" ]]; then
  log "❌ ファイルが見つかりません: $ARCHIVE"
  exit 1
fi

log "  アーカイブ: $ARCHIVE ($(du -sh "$ARCHIVE" | cut -f1))"

# parquet_output がある場合は確認
if [[ -d "./parquet_output" ]]; then
  EXISTING=$(ls ./parquet_output/*.parquet 2>/dev/null | wc -l | tr -d ' ')
  if [[ "$EXISTING" -gt 0 ]]; then
    log "⚠️  既存の parquet_output に $EXISTING ファイルがあります。"
    log "   上書きされます。Ctrl+C で中断してください..."
    sleep 3
  fi
fi

log "▶ 展開中..."
START=$(date +%s)
tar xzf "$ARCHIVE" -C "$(pwd)"
END=$(date +%s)
ELAPSED=$(( END - START ))

RESTORED=$(ls ./parquet_output/*.parquet 2>/dev/null | wc -l | tr -d ' ')
log "✅ 復元完了: ${ELAPSED}秒"
log "  復元ファイル数: $RESTORED 件"
log "  保存先: $(pwd)/parquet_output"
log ""
log "=== 復元完了 ==="
