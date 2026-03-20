#!/usr/bin/env bash
# parquet_output ディレクトリをローカルにバックアップするスクリプト
#
# 使い方:
#   bash scripts/backup-parquet.sh            # デフォルト: プロジェクト直下に保存
#   bash scripts/backup-parquet.sh /Volumes/外付けHDD  # 外付けHDDに保存
#
# 出力例: parquet_output_20260312.tar.gz
#         (4.5GB → 圧縮後 約2〜3GB)

set -euo pipefail
cd "$(dirname "$0")/.."

SOURCE_DIR="./parquet_output"
BACKUP_DIR="${1:-$(pwd)}"
TIMESTAMP=$(date '+%Y%m%d')
BACKUP_FILE="${BACKUP_DIR}/parquet_output_${TIMESTAMP}.tar.gz"
LOG="/tmp/backup_parquet.log"
SPLIT_SIZE="5G"              # 分割サイズ (デフォルト5GB)

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S JST')] $*" | tee -a "$LOG"; }

log "=== parquet_output バックアップ ==="
log ""

# 対象ディレクトリ確認
if [[ ! -d "$SOURCE_DIR" ]]; then
  log "❌ $SOURCE_DIR が見つかりません"
  exit 1
fi

FILE_COUNT=$(ls "$SOURCE_DIR" | wc -l | tr -d ' ')
PARQUET_COUNT=$(ls "$SOURCE_DIR"/*.parquet 2>/dev/null | wc -l | tr -d ' ')
DONE_COUNT=$(ls "$SOURCE_DIR"/*.done 2>/dev/null | wc -l | tr -d ' ')
SOURCE_SIZE=$(du -sh "$SOURCE_DIR" | cut -f1)

log "  対象ディレクトリ: $SOURCE_DIR"
log "  ファイル数: $FILE_COUNT 件 (.parquet: $PARQUET_COUNT, .done: $DONE_COUNT)"
log "  元サイズ:   $SOURCE_SIZE"
log "  保存先:     $BACKUP_FILE"
log ""

# 保存先ディレクトリ確認
if [[ ! -d "$BACKUP_DIR" ]]; then
  log "❌ 保存先ディレクトリが存在しません: $BACKUP_DIR"
  exit 1
fi

# 空き容量確認 (最低 3GB 必要)
AVAIL_KB=$(df -k "$BACKUP_DIR" | awk 'NR==2{print $4}')
AVAIL_GB=$(echo "scale=1; $AVAIL_KB / 1024 / 1024" | bc 2>/dev/null || echo "?")
log "  保存先空き容量: ${AVAIL_GB}GB"
if [[ "$AVAIL_KB" -lt 3145728 ]]; then
  log "⚠️  空き容量が 3GB 未満です。バックアップが失敗する可能性があります。"
fi
log ""

# -----------------------------------------------------------------------
# tar.gz 圧縮バックアップ
# -----------------------------------------------------------------------
log "▶ tar.gz 圧縮中... (数分かかります)"
START=$(date +%s)

tar czf "$BACKUP_FILE" \
  --exclude='parquet_output/*.done' \
  -C "$(dirname "$SOURCE_DIR")" \
  "$(basename "$SOURCE_DIR")"

END=$(date +%s)
ELAPSED=$(( END - START ))
BACKUP_SIZE=$(du -sh "$BACKUP_FILE" | cut -f1)

log "✅ 圧縮完了: ${ELAPSED}秒"
log "  バックアップサイズ: $BACKUP_SIZE"
log "  ファイル: $BACKUP_FILE"
log ""

# -----------------------------------------------------------------------
# 5GB 超の場合は自動分割
# -----------------------------------------------------------------------
FILE_BYTES=$(du -k "$BACKUP_FILE" | cut -f1)
if [[ "$FILE_BYTES" -gt 5242880 ]]; then
  log "▶ 5GB を超えるため分割します (${SPLIT_SIZE} 単位)..."
  SPLIT_PREFIX="${BACKUP_DIR}/parquet_output_${TIMESTAMP}_part_"
  split -b "$SPLIT_SIZE" "$BACKUP_FILE" "$SPLIT_PREFIX"
  PART_COUNT=$(ls "${SPLIT_PREFIX}"* | wc -l | tr -d ' ')
  log "  分割数: $PART_COUNT ファイル"
  ls -lh "${SPLIT_PREFIX}"* | tee -a "$LOG"
  log ""
  log "  復元コマンド:"
  log "    cat ${BACKUP_DIR}/parquet_output_${TIMESTAMP}_part_* > parquet_output_${TIMESTAMP}.tar.gz"
  log "    tar xzf parquet_output_${TIMESTAMP}.tar.gz"
else
  log "  復元コマンド:"
  log "    tar xzf $BACKUP_FILE"
fi

log ""
log "=== バックアップ完了 ==="
log "完了: $(date '+%Y-%m-%d %H:%M:%S JST')"
